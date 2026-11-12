"""RGB lighting: the Ambibox / SyncLight ambilight strip and Razer Chroma devices.

Everything here is fire-and-forget for the caller: each backend runs a worker thread that
coalesces requests to the newest one, rate-limits frames and talks to the hardware. Missing
hardware is a no-op, never an exception.

Strip (ROBOBLOQ / SyncLight controller, USB 1A86:FE07)
    When the Ambibox LED tray app is running it owns the strip (it holds the mutex
    ``Local\\AmbiboxLED``), so FreeDock sends JSON commands to its local control channel on
    udp://127.0.0.1:47811 (Ambibox LED 0.3.0+) and never opens the device. Overrides from
    FreeDock are temporary: the tray returns to the user's own mode (e.g. ambilight) on
    ``restore`` or a couple of seconds after FreeDock stops sending. A tray that holds the
    mutex but does not answer is an older version: FreeDock then stays off the strip
    ("tray-busy") rather than becoming a second writer.
    Without the tray, FreeDock drives the strip directly with a minimal copy of the protocol.

Razer Chroma
    The Chroma SDK REST API served by Razer Synapse on http://localhost:54235/razer/chromasdk.
    To enable it in Synapse 4: install the "Chroma" module (Synapse > Modules, sometimes listed
    as "Chroma Connect"), then in Chroma > Apps (Chroma Connect) turn on "Allow apps to control
    lighting" (and leave FreeDock enabled in the app list once it shows up). Without that, the
    port does not answer; FreeDock logs it once and retries every 30 s.

The strip protocol below is a trimmed copy of Ambibox LED's protocol.py,
https://github.com/agentkyo/ambibox-led-ambilight (MIT, same author); see its docs/PROTOCOL.md.
"""
from __future__ import annotations

import ctypes
import http.client
import json
import logging
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

log = logging.getLogger(__name__)

Color = tuple[int, int, int]
Target = str  # "all" | "strip" | "chroma"


def _byte(value) -> int:
    return max(0, min(255, int(value)))


def rgb_tuple(color: Iterable[int]) -> Color:
    r, g, b = color
    return _byte(r), _byte(g), _byte(b)


# =========================================================================== bar / level meter

def gradient_at(colors: Sequence[Color], t: float) -> Color:
    """Color at position t (0..1) of a gradient through `colors` (one color = solid)."""
    stops = [rgb_tuple(c) for c in colors] or [(255, 255, 255)]
    if len(stops) == 1:
        return stops[0]
    t = max(0.0, min(1.0, t)) * (len(stops) - 1)
    i = min(int(t), len(stops) - 2)
    f = t - i
    a, b = stops[i], stops[i + 1]
    return tuple(round(a[k] + (b[k] - a[k]) * f) for k in range(3))  # type: ignore[return-value]


def bar_colors(ratio: float, n: int, colors: Sequence[Color], off: Color = (0, 0, 0),
               mirror: bool = False) -> list[Color]:
    """n cells of a level meter filled to `ratio`. Each lit cell keeps the gradient color of
    its position, so a green-yellow-red meter turns red only near the top. mirror=True fills
    from both ends toward the middle (both sides of a monitor rise together)."""
    if n <= 0:
        return []
    ratio = max(0.0, min(1.0, float(ratio)))
    if mirror:
        half = (n + 1) // 2
        side = bar_colors(ratio, half, colors, off)
        return (side + side[: n - half][::-1])[:n] if n > 1 else side
    lit = round(ratio * n)
    span = max(1, n - 1)
    return [gradient_at(colors, i / span) if i < lit else rgb_tuple(off) for i in range(n)]


# =========================================================================== strip protocol (vendored)
# Trimmed from ambibox_led/protocol.py (MIT). All values big endian; checksum = sum & 0xFF.

VENDOR_ID, PRODUCT_ID, VENDOR_USAGE_PAGE = 0x1A86, 0xFE07, 0xFF00
REPORT_SIZE = 64
CMD_SYNC, CMD_EFFECT, CMD_SECTION, CMD_BRIGHTNESS = 0x80, 0x85, 0x86, 0x87
EFFECT_DYNAMIC = 2
TO_END, MAX_LEDS = 254, 253
STRIP_EFFECTS = ("rainbow", "breathing", "chase", "meteor", "sparkle", "gradient", "marquee")


def checksum(data: bytes) -> int:
    return sum(data) & 0xFF


def rb_packet(command: int, payload: bytes, msg_id: int) -> bytes:
    """'RB' len id cmd payload checksum; always one report."""
    total = 6 + len(payload)
    if total > REPORT_SIZE:
        raise ValueError("RB packet does not fit in one report")
    body = b"RB" + bytes((total, msg_id, command)) + payload
    return body + bytes((checksum(body),))


def sync_packet(colors: Sequence[Color], msg_id: int) -> bytes:
    """'SC' len16 id 0x80 [n R G B n]*N checksum. Spans several reports."""
    if not 1 <= len(colors) <= MAX_LEDS:
        raise ValueError(f"a sync frame carries 1..{MAX_LEDS} LEDs")
    data = bytearray()
    for n, color in enumerate(colors, start=1):
        data.append(n)
        data += bytes(rgb_tuple(color))
        data.append(n)
    body = b"SC" + (7 + len(data)).to_bytes(2, "big") + bytes((msg_id, CMD_SYNC)) + data
    return bytes(body) + bytes((checksum(body),))


def to_reports(packet: bytes) -> list[bytes]:
    return [packet[i:i + REPORT_SIZE].ljust(REPORT_SIZE, b"\0") for i in range(0, len(packet), REPORT_SIZE)]


def fill_packet(color: Color, msg_id: int) -> bytes:
    return rb_packet(CMD_SECTION, bytes((1, *rgb_tuple(color), TO_END)), msg_id)


def brightness_packet(value: int, msg_id: int) -> bytes:
    return rb_packet(CMD_BRIGHTNESS, bytes((max(5, min(255, int(value))),)), msg_id)


def effect_packet(index: int, msg_id: int) -> bytes:
    if not 0 <= index < len(STRIP_EFFECTS):
        raise ValueError("effect index must be 0..6")
    return rb_packet(CMD_EFFECT, bytes((EFFECT_DYNAMIC, index)), msg_id)


def fit_frame(colors: Sequence[Color], count: int) -> list[Color]:
    """Exactly `count` colors. More LEDs than the strip has hangs the controller until it is
    unplugged, so extra colors are dropped; missing ones are black."""
    out = [rgb_tuple(c) for c in colors[:count]]
    return out + [(0, 0, 0)] * (count - len(out))


def effect_index(name_or_index: str | int) -> int:
    if isinstance(name_or_index, int) and not isinstance(name_or_index, bool):
        index = name_or_index
    elif isinstance(name_or_index, str) and name_or_index.strip().isdigit():
        index = int(name_or_index)
    elif isinstance(name_or_index, str) and name_or_index.strip().lower() in STRIP_EFFECTS:
        index = STRIP_EFFECTS.index(name_or_index.strip().lower())
    else:
        raise ValueError(f"unknown effect {name_or_index!r}; use 0-6 or {', '.join(STRIP_EFFECTS)}")
    if not 0 <= index < len(STRIP_EFFECTS):
        raise ValueError("effect index must be 0..6")
    return index


# =========================================================================== strip transports

TRAY_HOST, TRAY_PORT = "127.0.0.1", 47811
TRAY_MUTEX = "AmbiboxLED"
LEDS_TTL_MS = 2000          # tray falls back to its own mode if frames stop for this long
KEEPALIVE_S = 0.8           # resend a still frame this often so the tray ttl never runs out
STRIP_FPS = 30.0
STATUS_TIMEOUT_S = 0.25


def tray_running(name: str = TRAY_MUTEX) -> bool:
    """True when the Ambibox LED app holds its single-instance lock. Never creates the lock."""
    if sys.platform == "win32":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenMutexW.restype = ctypes.c_void_p
        kernel32.OpenMutexW.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p]
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel32.OpenMutexW(0x00100000, False, f"Local\\{name}")  # SYNCHRONIZE
        if handle:
            kernel32.CloseHandle(handle)
            return True
        return False
    try:
        import fcntl
    except ImportError:
        return False
    path = Path(os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()) / f"{name}.lock"
    if not path.exists():
        return False
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return True
        fcntl.flock(lock, fcntl.LOCK_UN)
    return False


class UdpTransport:
    """JSON datagrams to the tray's control channel. Used from one worker thread only."""

    def __init__(self, host: str = TRAY_HOST, port: int = TRAY_PORT) -> None:
        self.addr = (host, port)
        self._sock: socket.socket | None = None

    def _socket(self) -> socket.socket:
        if self._sock is None:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        return self._sock

    def send(self, command: dict) -> None:
        try:
            self._socket().sendto(json.dumps(command, separators=(",", ":")).encode(), self.addr)
        except OSError as exc:
            log.debug("strip: send failed: %s", exc)

    def request(self, command: dict, timeout: float = STATUS_TIMEOUT_S) -> dict | None:
        sock = self._socket()
        sock.setblocking(False)
        try:
            while True:  # drop stale replies
                sock.recvfrom(65535)
        except OSError:
            pass
        try:
            sock.settimeout(timeout)
            sock.sendto(json.dumps(command).encode(), self.addr)
            data, _ = sock.recvfrom(65535)
            reply = json.loads(data)
            return reply if isinstance(reply, dict) else None
        except (OSError, ValueError):  # timeout, or WSAECONNRESET when nobody listens
            return None

    def close(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None


class DirectStrip:
    """The controller itself, through hidapi, for when the tray app is not running."""

    STALL_S = 0.3

    def __init__(self, led_count: int, brightness: int = 255) -> None:
        self.led_count = led_count
        self.brightness = brightness
        self._dev = None
        self._id = 0

    @staticmethod
    def _paths() -> list[bytes]:
        try:
            import hid
        except ImportError:
            return []
        try:
            infos = hid.enumerate(VENDOR_ID, PRODUCT_ID)
        except Exception:
            return []
        return [i["path"] for i in infos if i.get("usage_page") == VENDOR_USAGE_PAGE
                or (not i.get("usage_page") and i.get("interface_number") == 0)]

    @classmethod
    def present(cls) -> bool:
        return bool(cls._paths())

    @property
    def is_open(self) -> bool:
        return self._dev is not None

    def next_id(self) -> int:
        self._id = self._id % 254 + 1
        return self._id

    def open(self) -> bool:
        if self._dev is not None:
            return True
        paths = self._paths()
        if not paths:
            return False
        import hid
        dev = hid.device()
        try:
            dev.open_path(paths[0])
        except OSError as exc:
            log.warning("strip: could not open the controller: %s", exc)
            return False
        self._dev = dev
        self.send(brightness_packet(self.brightness, self.next_id()))
        return self._dev is not None

    def send(self, packet: bytes) -> bool:
        dev = self._dev
        if dev is None:
            return False
        # The reports of one packet must go back to back, or the firmware drops the frame.
        for report in to_reports(packet):
            started = time.perf_counter()
            try:
                written = dev.write(b"\0" + report)
            except (OSError, ValueError):
                written = -1
            if written < 0 or time.perf_counter() - started > self.STALL_S:
                log.warning("strip: controller stopped responding; closing it")
                self.close()
                return False
        return True

    def fill(self, color: Color) -> bool:
        return self.send(fill_packet(color, self.next_id()))

    def show(self, colors: Sequence[Color]) -> bool:
        return self.send(sync_packet(fit_frame(colors, self.led_count), self.next_id()))

    def effect(self, index: int) -> bool:
        return self.send(effect_packet(index, self.next_id()))

    def set_brightness(self, value: int) -> bool:
        self.brightness = value
        return self.send(brightness_packet(value, self.next_id()))

    def close(self) -> None:
        if self._dev is not None:
            try:
                self._dev.close()
            except Exception:
                pass
            self._dev = None


# =========================================================================== worker base

class _Worker:
    """A daemon thread that runs self._tick(now) -> seconds to wait, woken early by _wake()."""

    name = "lighting"

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self._cond = threading.Condition()
        self._thread: threading.Thread | None = None
        self._stopping = False

    def _wake(self) -> None:
        with self._cond:
            if self._thread is None and not self._stopping:
                self._thread = threading.Thread(target=self._loop, name=self.name, daemon=True)
                self._thread.start()
            self._cond.notify()

    def _loop(self) -> None:
        while True:
            with self._cond:
                if self._stopping:
                    return
            failed = False
            try:
                wait = self._tick(self.clock())
            except Exception:
                log.exception("%s worker", self.name)
                wait, failed = 1.0, True
            with self._cond:
                if self._stopping:
                    return
                # after a failure always pause: retrying at once would spin a core
                if wait > 0 and (failed or not self._has_work()):
                    self._cond.wait(min(wait, 5.0))

    def _has_work(self) -> bool:
        return False

    def _tick(self, now: float) -> float:
        raise NotImplementedError

    def stop(self, timeout: float = 2.0) -> None:
        with self._cond:
            self._stopping = True
            self._cond.notify()
            thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)


# =========================================================================== Ambibox strip client

class AmbiboxStrip(_Worker):
    """The ambilight strip, through the Ambibox LED tray app when it runs, else directly.

    mode: "tray" (control channel answers), "tray-busy" (an older tray holds the strip and has
    no control channel: upgrade Ambibox LED to 0.3.0), "direct", "absent" or "unknown".
    """

    name = "lighting-strip"

    def __init__(self, led_count: int = 71, *, transport: UdpTransport | None = None,
                 tray_probe: Callable[[], bool] = tray_running,
                 direct: Callable[[int], DirectStrip] = DirectStrip,
                 present: Callable[[], bool] | None = None,
                 clock: Callable[[], float] = time.monotonic, fps: float = STRIP_FPS) -> None:
        super().__init__(clock)
        self.led_count = max(1, min(MAX_LEDS, int(led_count)))
        self.tx = transport or UdpTransport()
        self._tray_probe = tray_probe
        self._direct_factory = direct
        self._present = present or DirectStrip.present
        self._direct: DirectStrip | None = None
        self.mode = "unknown"
        self.tray_count: int | None = None
        self.interval = 1.0 / fps
        self._queue: deque[dict] = deque(maxlen=32)
        self._frame: dict | None = None       # newest color/leds command not sent yet
        self._active: dict | None = None      # last frame sent, resent as keepalive when wanted
        self._keepalive = False
        self._last_frame = -1e9
        self._resend_at = 0.0
        self._next_detect = 0.0
        self._direct_deadline: float | None = None
        self._warned: set[str] = set()

    # -- public, called from any thread; never blocks on I/O

    def configure(self, led_count: int) -> None:
        with self._cond:
            self.led_count = max(1, min(MAX_LEDS, int(led_count)))
            if self._direct is not None:
                self._direct.led_count = self.led_count

    def color(self, rgb: Iterable[int], ttl_ms: int | None = None) -> None:
        self._set_frame({"cmd": "color", "rgb": list(rgb_tuple(rgb)), **({"ttl_ms": int(ttl_ms)} if ttl_ms else {})},
                        keepalive=False)

    def leds(self, colors: Sequence[Color], ttl_ms: int = LEDS_TTL_MS, keepalive: bool = True) -> None:
        frame = [list(rgb_tuple(c)) for c in colors[:MAX_LEDS]]
        if frame:
            self._set_frame({"cmd": "leds", "colors": frame, "ttl_ms": int(ttl_ms)}, keepalive=keepalive)

    def bar(self, ratio: float, colors: Sequence[Color], mirror: bool = False) -> None:
        self.leds(bar_colors(ratio, self.count(), colors, mirror=mirror))

    def flash(self, rgb: Iterable[int], times: int = 3, on_ms: int = 180, off_ms: int = 120) -> None:
        self._push({"cmd": "flash", "rgb": list(rgb_tuple(rgb)), "times": max(1, min(20, int(times))),
                    "on_ms": max(20, int(on_ms)), "off_ms": max(0, int(off_ms))})

    def effect(self, name_or_index: str | int) -> None:
        self._push({"cmd": "effect", "index": effect_index(name_or_index)}, clear_frame=True)

    def off(self) -> None:
        self._push({"cmd": "off"}, clear_frame=True)

    def brightness(self, value: int) -> None:
        self._push({"cmd": "brightness", "value": max(5, min(255, int(value)))})

    def restore(self) -> None:
        self._push({"cmd": "restore"}, clear_frame=True)

    def count(self) -> int:
        return min(self.led_count, self.tray_count or self.led_count)

    def status(self) -> str:
        if self.mode == "unknown":
            self._wake()  # first look happens on the worker
        return self.mode

    def stop(self, timeout: float = 2.0) -> None:
        super().stop(timeout)
        if self._direct is not None:
            self._direct.close()
        self.tx.close()

    # -- queueing

    def _set_frame(self, command: dict, keepalive: bool) -> None:
        with self._cond:
            self._frame = command
            self._keepalive = keepalive
        self._wake()

    def _push(self, command: dict, clear_frame: bool = False) -> None:
        with self._cond:
            # a newer discrete command makes an unsent frame stale
            self._frame = None
            if clear_frame:
                self._active = None
                self._keepalive = False
            self._queue.append(command)
        self._wake()

    def _has_work(self) -> bool:
        return bool(self._queue) or (self._frame is not None and self.clock() - self._last_frame >= self.interval)

    # -- worker

    def _tick(self, now: float) -> float:
        if now >= self._next_detect:
            self._detect(now)
        with self._cond:
            command = self._queue.popleft() if self._queue else None
        if command is not None:
            self._dispatch(command, now)
            return 0.0
        waits = [self._next_detect - now]
        with self._cond:
            frame = self._frame
            if frame is not None and now - self._last_frame >= self.interval:
                self._frame = None
                self._active = frame
            elif frame is not None:
                waits.append(self._last_frame + self.interval - now)
                frame = None
            elif self._keepalive and self._active is not None and now >= self._resend_at:
                frame = self._active
        if frame is not None:
            self._last_frame = now
            self._resend_at = now + KEEPALIVE_S
            self._dispatch(frame, now)
            return 0.0
        if self._keepalive and self._active is not None:
            waits.append(self._resend_at - now)
        if self._direct_deadline is not None:
            if now >= self._direct_deadline:
                self._direct_deadline = None
                self._direct_restore()
            else:
                waits.append(self._direct_deadline - now)
        return max(0.0, min(waits))

    def _detect(self, now: float) -> None:
        if self._tray_probe():
            if self._direct is not None:
                log.info("strip: Ambibox LED started; handing the strip over to it")
                self._direct.close()
                self._direct = None
            reply = self.tx.request({"cmd": "status"})
            if reply is not None:
                count = reply.get("count")
                self.tray_count = count if isinstance(count, int) and count > 0 else None
                self._set_mode("tray")
                self._next_detect = now + 5.0
            else:
                self.tray_count = None
                self._set_mode("tray-busy")
                self._next_detect = now + 3.0
        else:
            self.tray_count = None
            if self._direct is not None and self._direct.is_open:
                self._set_mode("direct")
            else:
                self._set_mode("direct" if self._present() else "absent")
            self._next_detect = now + 2.0

    def _set_mode(self, mode: str) -> None:
        if mode != self.mode:
            log.info("strip: %s", {
                "tray": "driving it through the Ambibox LED tray app",
                "tray-busy": "Ambibox LED is running without a control channel (needs 0.3.0+); leaving the strip alone",
                "direct": "Ambibox LED not running; driving the controller directly",
                "absent": "no strip found"}.get(mode, mode))
            self.mode = mode

    def _dispatch(self, command: dict, now: float) -> None:
        if self.mode == "tray":
            if command["cmd"] == "leds":
                command = {**command, "colors": command["colors"][: self.count()]}
            self.tx.send(command)
        elif self.mode == "direct":
            self._direct_run(command, now)
        # tray-busy / absent: nothing to drive

    # -- direct mode

    def _device(self) -> DirectStrip | None:
        if self._direct is None:
            self._direct = self._direct_factory(self.led_count)
        if not self._direct.open():
            self._direct = None
            return None
        return self._direct

    def _direct_run(self, command: dict, now: float) -> None:
        kind = command["cmd"]
        if kind == "restore":
            self._direct_deadline = None
            self._direct_restore()
            return
        dev = self._device()
        if dev is None:
            return
        if kind == "color":
            dev.fill(command["rgb"])
            ttl = command.get("ttl_ms")
            self._direct_deadline = now + ttl / 1000 if ttl else None
        elif kind == "leds":
            dev.show(command["colors"])
            self._direct_deadline = None  # the keepalive / caller decides; FreeDock exiting closes it
        elif kind == "off":
            dev.fill((0, 0, 0))
        elif kind == "effect":
            dev.fill((0, 0, 0))
            time.sleep(0.08)
            dev.effect(command["index"])
        elif kind == "brightness":
            dev.set_brightness(command["value"])
        elif kind == "flash":
            self._direct_flash(dev, command)

    def _direct_flash(self, dev: DirectStrip, command: dict) -> None:
        for _ in range(command["times"]):
            for color, ms in ((command["rgb"], command["on_ms"]), ((0, 0, 0), command["off_ms"])):
                if not ms:
                    continue
                if not dev.fill(color) or self._interrupted(ms / 1000):
                    return
        with self._cond:
            active = self._active
        if active is not None:
            self._dispatch(active, self.clock())
        else:
            self._direct_restore()

    def _interrupted(self, seconds: float) -> bool:
        with self._cond:
            if self._cond.wait_for(lambda: self._queue or self._stopping, timeout=seconds):
                return True
        return False

    def _direct_restore(self) -> None:
        """No saved mode without the tray: switch the strip off and let go of it."""
        with self._cond:
            self._active = None
            self._keepalive = False
        if self._direct is not None:
            self._direct.fill((0, 0, 0))
            self._direct.close()
            self._direct = None


# =========================================================================== Razer Chroma

CHROMA_URL = "http://localhost:54235/razer/chromasdk"
CHROMA_DEVICES = ("keyboard", "mouse", "mousepad", "headset", "chromalink")
CHROMA_FPS = 20.0
HEARTBEAT_S = 10.0           # the SDK drops a session after 15 s without a call
RETRY_S = 30.0
INIT_SETTLE_S = 1.0          # effects sent right after init are often ignored
KB_ROWS, KB_COLS = 6, 22
# Chroma SDK keyboard grid: RZKEY_F1..F12 = row 0, cols 3..14; RZKEY_1..0 = row 1, cols 2..11
F_ROW = tuple((0, c) for c in range(3, 15))
NUMBER_ROW = tuple((1, c) for c in range(2, 12))

APP_INFO = {
    "title": "FreeDock",
    "description": "Open-source driver for the MiraBox N4 stream deck",
    "author": {"name": "agentkyo", "contact": "https://github.com/agentkyo/freedock"},
    "device_supported": list(CHROMA_DEVICES),
    "category": "application",
}


def bgr(rgb: Iterable[int]) -> int:
    """Chroma colors are 0x00BBGGRR integers."""
    r, g, b = rgb_tuple(rgb)
    return r | g << 8 | b << 16


def static_body(rgb: Iterable[int]) -> dict:
    return {"effect": "CHROMA_STATIC", "param": {"color": bgr(rgb)}}


NONE_BODY = {"effect": "CHROMA_NONE"}


def custom_body(grid: Sequence[Sequence[int]]) -> dict:
    return {"effect": "CHROMA_CUSTOM", "param": [list(row) for row in grid]}


def keyboard_grid(fill: Iterable[int] = (0, 0, 0)) -> list[list[int]]:
    color = bgr(fill)
    return [[color] * KB_COLS for _ in range(KB_ROWS)]


def keyboard_bar(ratio: float, colors: Sequence[Color], row: str = "f",
                 background: Iterable[int] = (0, 0, 0)) -> list[list[int]]:
    """6x22 grid with the F-row (row="f") or the number row (row="number") as a level meter."""
    cells = NUMBER_ROW if row in ("number", "numbers", "num", "1") else F_ROW
    grid = keyboard_grid(background)
    for (r, c), color in zip(cells, bar_colors(ratio, len(cells), colors, off=rgb_tuple(background))):
        grid[r][c] = bgr(color)
    return grid


def urllib_http(method: str, url: str, body: dict | None = None, timeout: float = 2.0) -> dict | None:
    """One JSON request. Raises OSError when the server is unreachable or answers an error."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # localhost, never a proxy
    try:
        with opener.open(request, timeout=timeout) as response:
            text = response.read().decode("utf-8", "replace")
    except (urllib.error.URLError, http.client.HTTPException) as exc:
        # HTTPException covers servers that answer garbage (BadStatusLine), seen with Synapse
        raise OSError(str(exc)) from exc
    try:
        reply = json.loads(text) if text.strip() else None
    except ValueError:
        return None
    return reply if isinstance(reply, dict) else None


class ChromaClient(_Worker):
    """Razer Chroma SDK REST client on a background thread.

    POST {url} with APP_INFO -> {"sessionid", "uri"}; PUT {uri}/heartbeat every 10 s;
    PUT {uri}/{device} with CHROMA_STATIC / CHROMA_CUSTOM / CHROMA_NONE; DELETE {uri} releases
    the session, which hands the lighting back to Synapse. Requests are coalesced per device
    and sent at most CHROMA_FPS times a second.

    Enable it in Razer Synapse 4: add the Chroma module (Modules > "Chroma" / "Chroma Connect"),
    then under Chroma > Apps turn on "Allow apps to control lighting" / Chroma Connect, and keep
    FreeDock allowed in the app list. Until then the SDK port does not answer and this client
    logs once and retries every 30 s.
    """

    name = "lighting-chroma"

    def __init__(self, url: str = CHROMA_URL, *, http: Callable[..., dict | None] = urllib_http,
                 clock: Callable[[], float] = time.monotonic, fps: float = CHROMA_FPS,
                 app: dict | None = None) -> None:
        super().__init__(clock)
        self.url = url
        self.http = http
        self.app = app or APP_INFO
        self.interval = 1.0 / fps
        self.session: str | None = None
        self._pending: dict[str, dict] = {}   # device -> newest body not sent yet
        self._current: dict[str, dict] = {}   # what is shown, to come back to after a flash
        self._release = False
        self._script: deque[tuple[float, dict[str, dict] | None]] = deque()  # (at, bodies | None=end)
        self._deadline: float | None = None   # ttl of a color
        self._retry_at = 0.0
        self._settle_until = 0.0
        self._last_send = -1e9
        self._last_beat = 0.0
        self._announced = False

    @property
    def connected(self) -> bool:
        return self.session is not None

    def status(self) -> str:
        return "connected" if self.session else "absent"

    # -- public, any thread

    def set(self, device: str, body: dict, *, remember: bool = True) -> None:
        with self._cond:
            self._script.clear()
            self._release = False
            self._pending[device] = body
            if remember:
                self._current[device] = body
        self._wake()

    def static(self, rgb: Iterable[int], devices: Iterable[str] = CHROMA_DEVICES, ttl_ms: int | None = None) -> None:
        body = static_body(rgb)
        with self._cond:
            self._script.clear()
            self._release = False
            for device in devices:
                self._pending[device] = self._current[device] = body
            self._deadline = self.clock() + ttl_ms / 1000 if ttl_ms else None
        self._wake()

    def keyboard(self, grid: Sequence[Sequence[int]]) -> None:
        with self._cond:
            self._deadline = None
        self.set("keyboard", custom_body(grid))

    def none(self, devices: Iterable[str] = CHROMA_DEVICES) -> None:
        for device in devices:
            self.set(device, NONE_BODY)

    def flash(self, rgb: Iterable[int], times: int = 3, on_ms: int = 180, off_ms: int = 120,
              devices: Iterable[str] = CHROMA_DEVICES) -> None:
        devices = tuple(devices)
        on = {d: static_body(rgb) for d in devices}
        off = {d: NONE_BODY for d in devices}
        with self._cond:
            at = self.clock()
            self._script.clear()
            self._release = False
            for _ in range(max(1, int(times))):
                self._script.append((at, on))
                at += max(self.interval, on_ms / 1000)
                if off_ms:
                    self._script.append((at, off))
                    at += max(self.interval, off_ms / 1000)
            self._script.append((at, None))
        self._wake()

    def release(self) -> None:
        """Give the lighting back to Synapse."""
        with self._cond:
            self._script.clear()
            self._pending.clear()
            self._current.clear()
            self._deadline = None
            self._release = True
        self._wake()

    def stop(self, timeout: float = 2.0) -> None:
        super().stop(timeout)
        if self.session:
            try:
                self.http("DELETE", self.session)
            except OSError:
                pass
            self.session = None

    def _has_work(self) -> bool:
        # pending colours only count once there is a session; until Synapse answers, the retry
        # timer decides when to try again (otherwise this loop busy-waits for the whole retry delay)
        return self._release or (bool(self._pending) and self.session is not None
                                 and self.clock() - self._last_send >= self.interval)

    # -- worker

    def _tick(self, now: float) -> float:
        waits = []
        with self._cond:
            release = self._release
            self._release = False
            if self._deadline is not None and now >= self._deadline:
                self._deadline = None
                release = True
                self._current.clear()
                self._pending.clear()
            while self._script and self._script[0][0] <= now:
                _, bodies = self._script.popleft()
                if bodies is None:  # flash over: back to what was shown, or to Synapse
                    if self._current:
                        self._pending.update(self._current)
                    else:
                        release = True
                else:
                    self._pending.update(bodies)
            if self._script:
                waits.append(self._script[0][0] - now)
            if self._deadline is not None:
                waits.append(self._deadline - now)
            pending = bool(self._pending)
        if release and not pending:
            self._close()
        if pending and self.session is None:
            if now < self._retry_at:
                waits.append(self._retry_at - now)
                return max(0.0, min(waits))
            self._open(now)
            return 0.0 if self.session else max(0.0, min(waits + [self._retry_at - now]))
        if self.session is not None:
            if now - self._last_beat >= HEARTBEAT_S:
                self._call("PUT", f"{self.session}/heartbeat", None, now)
                self._last_beat = now
            waits.append(self._last_beat + HEARTBEAT_S - now)
            if pending:
                ready = max(self._settle_until, self._last_send + self.interval)
                if now >= ready:
                    with self._cond:
                        bodies, self._pending = self._pending, {}
                    self._last_send = now
                    for device, body in bodies.items():
                        if not self._call("PUT", f"{self.session}/{device}", body, now):
                            break
                    return 0.0
                waits.append(ready - now)
        return max(0.0, min(waits)) if waits else 5.0

    def _open(self, now: float) -> None:
        try:
            reply = self.http("POST", self.url, self.app)
        except OSError as exc:
            reply = None
            if not self._announced:
                log.info("chroma: SDK not answering at %s (%s); enable Chroma Connect in Synapse. "
                         "Retrying every %d s", self.url, exc, RETRY_S)
                self._announced = True
        uri = reply.get("uri") if reply else None
        if not uri:
            if reply is not None and not self._announced:
                log.info("chroma: init refused: %s", reply)
                self._announced = True
            self._retry_at = now + RETRY_S
            return
        log.info("chroma: connected (session %s)", reply.get("sessionid", reply.get("session")))
        self.session = uri.rstrip("/")
        self._announced = False
        self._settle_until = now + INIT_SETTLE_S
        self._last_beat = now

    def _call(self, method: str, url: str, body: dict | None, now: float) -> bool:
        try:
            reply = self.http(method, url, body)
        except OSError as exc:
            log.info("chroma: lost the SDK (%s)", exc)
            self.session = None
            self._retry_at = now + RETRY_S
            return False
        if reply and reply.get("result") not in (None, 0):
            log.debug("chroma: %s %s -> %s", method, url, reply)
        return True

    def _close(self) -> None:
        if self.session is not None:
            try:
                self.http("DELETE", self.session)
            except OSError:
                pass
            self.session = None


# =========================================================================== facade

class Lights:
    """One entry point for every light FreeDock can drive. Nothing here blocks or raises on
    missing hardware. Targets: "all", "strip", "chroma"."""

    def __init__(self, strip: AmbiboxStrip | None = None, chroma: ChromaClient | None = None) -> None:
        self._strip = strip
        self._chroma = chroma
        self.strip_enabled = True
        self.chroma_enabled = True
        self.strip_count = 71
        self._lock = threading.Lock()
        self._scene_strip = False       # scene() is driving the strip / Chroma
        self._scene_chroma = False
        self._scene_glow: dict | None = None

    # -- backends, created on first use so importing this module starts nothing

    def _get_strip(self) -> AmbiboxStrip | None:
        if not self.strip_enabled:
            return None
        with self._lock:
            if self._strip is None:
                self._strip = AmbiboxStrip(self.strip_count)
            return self._strip

    def _get_chroma(self) -> ChromaClient | None:
        if not self.chroma_enabled:
            return None
        with self._lock:
            if self._chroma is None:
                self._chroma = ChromaClient()
            return self._chroma

    def _targets(self, target: Target):
        if target not in ("all", "strip", "chroma"):
            log.warning("lighting: unknown target %r", target)
            return None, None
        strip = self._get_strip() if target in ("all", "strip") else None
        chroma = self._get_chroma() if target in ("all", "chroma") else None
        return strip, chroma

    def configure(self, strip_enabled: bool = True, strip_count: int = 71, chroma_enabled: bool = True) -> None:
        self.strip_count = max(1, min(MAX_LEDS, int(strip_count)))
        if not strip_enabled and self.strip_enabled and self._strip is not None:
            self._strip.restore()
        if not chroma_enabled and self.chroma_enabled and self._chroma is not None:
            self._chroma.release()
        self.strip_enabled, self.chroma_enabled = bool(strip_enabled), bool(chroma_enabled)
        if self._strip is not None:
            self._strip.configure(self.strip_count)

    def color(self, rgb: Iterable[int], target: Target = "all", ttl_ms: int | None = None) -> None:
        rgb = rgb_tuple(rgb)
        strip, chroma = self._targets(target)
        if strip:
            strip.color(rgb, ttl_ms)
        if chroma:
            chroma.static(rgb, ttl_ms=ttl_ms)

    def flash(self, rgb: Iterable[int], times: int = 3, on_ms: int = 180, off_ms: int = 120,
              target: Target = "all") -> None:
        rgb = rgb_tuple(rgb)
        strip, chroma = self._targets(target)
        if strip:
            strip.flash(rgb, times, on_ms, off_ms)
        if chroma:
            chroma.flash(rgb, times, on_ms, off_ms)

    def bar(self, ratio: float, colors: Sequence[Color] = ((0, 255, 0), (255, 255, 0), (255, 0, 0)),
            target: Target = "all", kb_row: str = "f", mirror: bool = False) -> None:
        """Level meter / shift light: the strip lights a proportional run of LEDs (mirror=True
        rises from both ends), the keyboard lights the F-row (kb_row="f") or number row."""
        strip, chroma = self._targets(target)
        if strip:
            strip.bar(ratio, colors, mirror=mirror)
        if chroma:
            chroma.keyboard(keyboard_bar(ratio, colors, kb_row))

    def effect(self, name_or_index: str | int) -> None:
        """Built-in strip animation: 0-6 or rainbow, breathing, chase, meteor, sparkle, gradient, marquee."""
        strip = self._get_strip()
        if strip:
            try:
                strip.effect(name_or_index)
            except ValueError as exc:
                log.warning("lighting: %s", exc)

    # -- scenes: whole frames from integrations.game_lights

    def scene(self, keyboard: Sequence[Sequence[Iterable[int]]] | None, strip: Sequence[Color] | None,
              glow: Iterable[int] | None, devices: Iterable[str] = ("mouse", "mousepad", "headset", "chromalink")
              ) -> None:
        """One animation frame. keyboard: 6x22 RGB grid; strip: one color per LED; glow: the other Chroma
        devices. None = leave that light alone (a strip left alone goes back to the user's own mode)."""
        strip_dev, chroma = self._targets("all")
        if strip_dev is not None:
            if strip is not None:
                strip_dev.leds(strip)
                self._scene_strip = True
            elif self._scene_strip:
                strip_dev.restore()
                self._scene_strip = False
        if chroma is not None:
            if keyboard is not None:
                chroma.keyboard([[bgr(c) for c in row] for row in keyboard])
                self._scene_chroma = True
            if glow is not None:
                body = static_body(glow)
                last = self._scene_glow
                if last != body:                  # the other devices only when their color changes
                    for device in devices:
                        chroma.set(device, body)
                    self._scene_glow = body
                self._scene_chroma = True
            elif keyboard is None and self._scene_chroma:
                chroma.release()
                self._scene_chroma = False
                self._scene_glow = None

    def end_scene(self) -> None:
        """Hand back whatever scene() was driving."""
        if self._scene_strip and self._strip is not None:
            self._strip.restore()
        if self._scene_chroma and self._chroma is not None:
            self._chroma.release()
        self._scene_strip = self._scene_chroma = False
        self._scene_glow = None

    def restore(self, target: Target = "all") -> None:
        """Back to the user's own lighting: the tray's saved mode, Synapse's effects."""
        strip, chroma = (self._strip if target in ("all", "strip") else None,
                         self._chroma if target in ("all", "chroma") else None)
        if strip:
            strip.restore()
        if chroma:
            chroma.release()

    def status(self) -> dict:
        strip = self._get_strip()
        chroma = self._chroma if self.chroma_enabled else None
        return {
            "strip": strip.status() if strip else "disabled",
            "strip_count": strip.count() if strip else self.strip_count,
            "chroma": ("disabled" if not self.chroma_enabled else chroma.status() if chroma else "absent"),
        }

    def shutdown(self) -> None:
        """Hand everything back and stop the workers (call on app exit)."""
        for backend in (self._strip, self._chroma):
            if backend is None:
                continue
            if isinstance(backend, AmbiboxStrip):
                backend.restore()
            else:
                backend.release()
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and any(
                b is not None and b._has_work() for b in (self._strip, self._chroma)):
            time.sleep(0.02)
        for backend in (self._strip, self._chroma):
            if backend is not None:
                backend.stop()


lights = Lights()
