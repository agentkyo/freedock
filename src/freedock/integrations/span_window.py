"""Stretch the truck games' window over every monitor (triple screens without NVIDIA Surround).

ETS2 / ATS support three monitors only as one big screen (r_multimon_mode 2-4, see the SCS wiki
"Multi monitor configuration"). Without Surround - here impossible, the middle monitor is an
ultrawide and the sides are 16:9 - the game can run in a borderless window as wide as the whole
desktop, but at every start it shrinks that window into the primary monitor. This puts it back.

It only acts when the player asked for it in the game's own config.cfg: a windowed (not
fullscreen) mode whose size is the whole Windows desktop. Anything else is left alone.
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

log = logging.getLogger(__name__)

GAMES = {"eurotrucks2.exe": "Euro Truck Simulator 2", "amtrucks.exe": "American Truck Simulator"}
MIN_WIDTH = 400                      # the game opens tiny (116x1) windows while it starts


def wanted_size(config_text: str) -> tuple[int, int] | None:
    """The window size config.cfg asks for, when it is a windowed mode; None for fullscreen."""
    vals = dict(re.findall(r'^uset (r_mode_width|r_mode_height|r_fullscreen) "([^"]*)"', config_text, re.M))
    try:
        if vals.get("r_fullscreen", "1") != "0":
            return None
        return int(vals["r_mode_width"]), int(vals["r_mode_height"])
    except (KeyError, ValueError):
        return None


def target_rect(size: tuple[int, int] | None, desktop: tuple[int, int, int, int]) -> tuple[int, int, int, int] | None:
    """(x, y, w, h) to put the window at: the whole desktop, only when config.cfg asks for exactly it."""
    if size is None:
        return None
    x, y, w, h = desktop
    return desktop if size == (w, h) else None


class WindowSpanner:
    """Called with the running process names (FreeDock already scans them for the auto pages)."""

    def __init__(self, documents: Path | None = None):
        self.documents = documents

    def _config(self, exe: str) -> str:
        docs = self.documents
        if docs is None:
            from .ets2 import _documents
            docs = _documents()
        try:
            return (docs / GAMES[exe] / "config.cfg").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def update(self, running: set[str]) -> None:
        if sys.platform != "win32" or not running & GAMES.keys():
            return
        try:
            for exe in running & GAMES.keys():
                self._span(exe)
        except Exception:  # noqa: BLE001 - never break the engine loop over a window
            log.debug("span: failed", exc_info=True)

    def _span(self, exe: str) -> None:
        target = target_rect(wanted_size(self._config(exe)), _desktop())
        if target is None:
            return
        for _pid, hwnd, rect in _windows(exe):
            if rect[2] < MIN_WIDTH or rect == target:
                continue
            if _move(hwnd, target):
                log.info("span: %s window %s -> %s", exe, rect, target)


# ---------------------------------------------------------------- Win32
def _desktop() -> tuple[int, int, int, int]:
    import ctypes
    m = ctypes.windll.user32.GetSystemMetrics
    return m(76), m(77), m(78), m(79)       # SM_X/YVIRTUALSCREEN, SM_CX/CYVIRTUALSCREEN


def _windows(exe: str) -> list[tuple[int, int, tuple[int, int, int, int]]]:
    """Visible top-level windows of the game's process: (pid, hwnd, (x, y, w, h))."""
    import ctypes
    from ctypes import wintypes

    import psutil
    pids = {p.pid for p in psutil.process_iter(["name"]) if (p.info.get("name") or "").lower() == exe}
    user32 = ctypes.windll.user32
    out: list[tuple[int, int, tuple[int, int, int, int]]] = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # GW_OWNER
            r = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            out.append((pid.value, hwnd, (r.left, r.top, r.right - r.left, r.bottom - r.top)))
        return True

    user32.EnumWindows(each, 0)
    return out


def _move(hwnd: int, rect: tuple[int, int, int, int]) -> bool:
    import ctypes
    SWP_NOZORDER, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x4, 0x10, 0x40
    x, y, w, h = rect
    return bool(ctypes.windll.user32.SetWindowPos(hwnd, None, x, y, w, h, SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW))
