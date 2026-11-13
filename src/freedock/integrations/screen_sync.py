"""Screen sync: when no game is on, the keyboard follows the picture on the monitor, like the strip.

The Ambibox LED tray drives the strip from the screen edges but does not hand its colors out, so
FreeDock samples the primary monitor itself, cheaply: GDI point-samples the screen into a small
bitmap (no full-resolution copy), numpy averages it down to the Chroma keyboard grid (6 x 22), and
the colors get the same treatment as the strip (black level, saturation, ~80 ms smoothing). The
keyboard becomes a small copy of the screen; mouse, mousepad and headset take its average color.

A dark screen never turns the keyboard off: keys below FLOOR glow dimly in the picture's overall
hue (or FreeDock blue on a black screen), so it stays readable.

Windows only; elsewhere make_sampler() returns None and nothing changes. FREEDOCK_NO_SCREEN=1
turns it off (the tests set it).
"""
from __future__ import annotations

import logging
import math
import os
import sys
import threading
import time
from collections.abc import Callable

import numpy as np

log = logging.getLogger(__name__)

ROWS, COLS = 6, 22
SAMPLE_W, SAMPLE_H = COLS * 8, ROWS * 16   # point samples: 8 x 16 per key
FPS = 15.0
SMOOTHING_MS = 80.0
SATURATION = 1.15
BLACK_LEVEL = 6
FLOOR = 34                                 # dimmest a key gets (max channel, 0-255)
DARK_ACCENT = (91, 140, 255)               # FreeDock blue, for an all-black screen

Grabber = Callable[[], "np.ndarray | None"]   # -> (h, w, 3) uint8 RGB, None on failure


# =========================================================================== pure color math

def to_grid(rgb: np.ndarray, rows: int = ROWS, cols: int = COLS) -> np.ndarray:
    """Average an (h, w, 3) picture down to (rows, cols, 3) floats."""
    h, w = rgb.shape[:2]
    h2, w2 = max(rows, h - h % rows), max(cols, w - w % cols)
    if h < rows or w < cols:   # tiny input: nearest-neighbour up to the grid
        ys = (np.arange(rows) * h // rows)
        xs = (np.arange(cols) * w // cols)
        return rgb[ys][:, xs].astype(np.float32)
    a = rgb[:h2, :w2].astype(np.float32)
    return a.reshape(rows, h2 // rows, cols, w2 // cols, 3).mean(axis=(1, 3))


def tune(grid: np.ndarray, saturation: float = SATURATION, black_level: int = BLACK_LEVEL) -> np.ndarray:
    """The strip's look: crush the near-blacks, then push the saturation a little."""
    g = np.clip((grid - black_level) * (255.0 / (255 - black_level)), 0, 255)
    gray = g.mean(axis=-1, keepdims=True)
    return np.clip(gray + (g - gray) * saturation, 0, 255)


def lift(grid: np.ndarray, floor: float = FLOOR, accent=DARK_ACCENT) -> np.ndarray:
    """Keep every key readable: dark keys glow at `floor` in the picture's overall hue."""
    avg = grid.reshape(-1, 3).mean(axis=0)
    peak = float(avg.max())
    base = np.asarray(accent, np.float32) if peak < 6 else avg
    hue = base * (floor / max(1.0, float(base.max())))
    v = grid.max(axis=-1, keepdims=True)
    weight = np.clip(1.0 - v / floor, 0.0, 1.0)
    return np.clip(grid + hue * weight, 0, 255)


def glow_of(grid: np.ndarray, step: int = 12) -> tuple[int, int, int]:
    """One color for the mouse / headset: the average, quantized so it is not resent every frame."""
    avg = grid.reshape(-1, 3).mean(axis=0)
    peak = float(avg.max())
    if peak > 1:   # the average of a busy picture is muddy; keep its hue at a usable brightness
        avg = avg * (max(peak, 90.0) / peak)
    q = np.round(np.clip(avg, 0, 255) / step) * step
    return tuple(int(min(255, x)) for x in q)


def as_rows(grid: np.ndarray) -> list[list[tuple[int, int, int]]]:
    g = np.rint(grid).astype(np.int32)
    return [[(int(p[0]), int(p[1]), int(p[2])) for p in row] for row in g]


# =========================================================================== Windows capture

def gdi_grabber(width: int = SAMPLE_W, height: int = SAMPLE_H) -> tuple[Grabber, Callable[[], None]] | None:
    """Point-sample the primary monitor into a width x height bitmap with GDI StretchBlt.

    Must be created and used on one thread (the sampler's). Returns (grab, close) or None."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
    HDC, HBITMAP = wintypes.HDC, wintypes.HBITMAP
    user32.GetDC.restype = HDC
    user32.GetDC.argtypes = [wintypes.HWND]
    user32.ReleaseDC.argtypes = [wintypes.HWND, HDC]
    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    gdi32.CreateCompatibleDC.restype = HDC
    gdi32.CreateCompatibleDC.argtypes = [HDC]
    gdi32.CreateDIBSection.restype = HBITMAP
    gdi32.CreateDIBSection.argtypes = [HDC, ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(ctypes.c_void_p),
                                       wintypes.HANDLE, wintypes.DWORD]
    gdi32.SelectObject.restype = wintypes.HGDIOBJ
    gdi32.SelectObject.argtypes = [HDC, wintypes.HGDIOBJ]
    gdi32.SetStretchBltMode.argtypes = [HDC, ctypes.c_int]
    gdi32.StretchBlt.argtypes = [HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                 HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
    gdi32.GdiFlush.argtypes = []
    gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
    gdi32.DeleteDC.argtypes = [HDC]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                    ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                    ("biXPelsPerMeter", wintypes.LONG), ("biYPelsPerMeter", wintypes.LONG),
                    ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    screen = user32.GetDC(None)
    if not screen:
        return None
    mem = gdi32.CreateCompatibleDC(screen)
    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), width, -height, 1, 32, 0, 0, 0, 0, 0, 0)  # top-down
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(mem, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    if not mem or not bmp or not bits:
        user32.ReleaseDC(None, screen)
        return None
    old = gdi32.SelectObject(mem, bmp)
    gdi32.SetStretchBltMode(mem, 3)    # COLORONCOLOR: point sampling, cheap at any resolution
    buf = (ctypes.c_uint8 * (width * height * 4)).from_address(bits.value)
    SRCCOPY = 0x00CC0020

    def grab() -> np.ndarray | None:
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)   # primary monitor
        if sw <= 0 or sh <= 0 or not gdi32.StretchBlt(mem, 0, 0, width, height, screen, 0, 0, sw, sh, SRCCOPY):
            return None                # locked screen, UAC prompt, display switching...
        gdi32.GdiFlush()
        bgra = np.frombuffer(buf, np.uint8).reshape(height, width, 4)
        return bgra[:, :, 2::-1].copy()

    def close() -> None:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)

    return grab, close


# =========================================================================== sampler thread

class ScreenSampler:
    """Background thread: the newest smoothed keyboard grid + glow color, ready for GameLights."""

    def __init__(self, opener: Callable[[], tuple[Grabber, Callable[[], None]] | None] = gdi_grabber,
                 fps: float = FPS, smoothing_ms: float = SMOOTHING_MS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.opener = opener
        self.interval = 1.0 / fps
        self.tau = smoothing_ms / 1000.0
        self.clock = clock
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._grid: np.ndarray | None = None
        self._latest: tuple[list, tuple[int, int, int]] | None = None
        self._t = 0.0
        self.failed = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running or self.failed:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="screen-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(1.0)
        self._thread = None
        with self._lock:
            self._latest = None
            self._grid = None

    def latest(self) -> tuple[list, tuple[int, int, int]] | None:
        with self._lock:
            return self._latest

    def feed(self, rgb: np.ndarray | None, now: float) -> None:
        """One captured picture (None = capture failed: drift to black, i.e. the dim floor)."""
        target = tune(to_grid(rgb)) if rgb is not None else np.zeros((ROWS, COLS, 3), np.float32)
        if self._grid is None:
            grid = target
        else:
            k = 1.0 - math.exp(-max(0.0, now - self._t) / self.tau) if self.tau > 0 else 1.0
            grid = self._grid + (target - self._grid) * k
        self._grid, self._t = grid, now
        shown = lift(grid)
        with self._lock:
            self._latest = (as_rows(shown), glow_of(shown))

    def _run(self) -> None:
        try:
            opened = self.opener()
        except Exception:
            log.exception("screen sync: capture failed to open")
            opened = None
        if opened is None:
            log.info("screen sync: no screen capture here; the keyboard stays with Synapse")
            self.failed = True
            return
        grab, close = opened
        log.info("screen sync: the keyboard follows the screen")
        try:
            while not self._stop.is_set():
                t0 = self.clock()
                try:
                    rgb = grab()
                except Exception:
                    rgb = None
                self.feed(rgb, t0)
                self._stop.wait(max(0.0, self.interval - (self.clock() - t0)))
        finally:
            close()


def make_sampler() -> ScreenSampler | None:
    if sys.platform != "win32" or os.environ.get("FREEDOCK_NO_SCREEN"):
        return None
    return ScreenSampler()
