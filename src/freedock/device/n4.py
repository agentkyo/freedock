"""Talking to a real N4 over hidapi: one reader thread, one writer thread, hotplug with reconnect.

The writer keeps only the *latest* image per slot. If the renderer produces frames faster than
USB drains them, stale frames are dropped instead of queueing up - animations stay in sync
with time instead of lagging further and further behind.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

import hid

from . import protocol
from .layout import MODELS, InputEvent, Model

log = logging.getLogger(__name__)

HEARTBEAT_SECONDS = 8.0
HOTPLUG_SECONDS = 1.5


@dataclass(frozen=True)
class DeviceInfo:
    model: Model
    path: bytes
    serial: str

    @property
    def label(self) -> str:
        return f"{self.model.name} ({self.serial})"


def enumerate_devices() -> list[DeviceInfo]:
    found = []
    for model in MODELS:
        for d in hid.enumerate(model.vendor_id, model.product_id):
            # hidraw/Windows report the vendor usage page; the libusb backend may report 0,
            # in which case the vendor interface is number 0 (interface 1 is the keyboard)
            vendor_iface = d.get("usage_page") == model.usage_page or (
                not d.get("usage_page") and d.get("interface_number") == 0)
            if vendor_iface:
                found.append(DeviceInfo(model, d["path"], d.get("serial_number") or "?"))
    return found


class N4Device:
    """An open N4. Thread-safe; all public methods may be called from any thread."""

    def __init__(self, info: DeviceInfo, on_input: Callable[[InputEvent], None],
                 on_lost: Callable[[], None]):
        self.info = info
        self.model = info.model
        self._on_input = on_input
        self._on_lost = on_lost
        self._dev = hid.device()
        self._dev.open_path(info.path)
        self._write_lock = threading.Lock()
        self._pending: dict[int, bytes] = {}
        self._pending_cmds: list[bytes] = []
        self._cond = threading.Condition()
        self._alive = True
        self._lost_reported = False
        self.firmware = self._read_firmware()
        # Claim the panel first: after a previous session handed it back to the firmware (CLE DC) the
        # panel ignores images until it hears CONNECT - the keys stayed black until a replug.
        self._send(protocol.heartbeat())
        self._resync()
        self._send(protocol.wake_screen())
        self._send(protocol.heartbeat())
        self._threads = [
            threading.Thread(target=self._reader, name="n4-reader", daemon=True),
            threading.Thread(target=self._writer, name="n4-writer", daemon=True),
        ]
        for t in self._threads:
            t.start()

    # ------------------------------------------------------------------ public API
    def set_image(self, image_id: int, jpeg: bytes) -> None:
        with self._cond:
            self._pending[image_id] = jpeg
            self._cond.notify()

    def set_brightness(self, percent: int) -> None:
        self._queue(protocol.brightness(percent))

    def clear(self, image_id: int = 0xFF) -> None:
        with self._cond:
            if image_id == 0xFF:
                self._pending.clear()
            else:
                self._pending.pop(image_id, None)
        self._queue(protocol.clear(image_id), protocol.flush())

    def sleep(self) -> None:
        self._queue(protocol.sleep_screen())

    def wake(self) -> None:
        self._queue(protocol.wake_screen())

    def close(self, hand_back: bool = True) -> None:
        if not self._alive:
            return
        self._alive = False
        with self._cond:
            self._cond.notify_all()
        try:
            if hand_back:
                self._send(protocol.clear())
                self._send(protocol.flush())
                self._send(protocol.shutdown())
        except OSError:
            pass
        time.sleep(0.05)
        try:
            self._dev.close()
        except Exception:  # noqa: BLE001 - closing a vanished device can raise anything
            pass

    # ------------------------------------------------------------------ internals
    def _queue(self, *reports: bytes) -> None:
        with self._cond:
            self._pending_cmds.extend(reports)
            self._cond.notify()

    def _send(self, report: bytes) -> None:
        with self._write_lock:
            written = self._dev.write(report)
        if written != len(report):
            raise OSError(f"short write ({written} of {len(report)})")

    def _resync(self) -> None:
        """Recover a panel left mid-upload by a program that died while sending an image.

        After a ``BAT`` header the firmware swallows the next *length* bytes as JPEG data, so a
        killed sender leaves it treating every later command as picture bytes - the screen
        freezes. Ten blank reports cover the largest possible image (10 KiB); then wipe and commit.
        """
        blank = b"\x00" * protocol.REPORT_SIZE
        for _ in range(protocol.MAX_IMAGE_BYTES // protocol.PACKET_SIZE):
            self._send(blank)
        self._send(protocol.flush())
        self._send(protocol.clear())
        self._send(protocol.flush())

    def _read_firmware(self) -> str | None:
        for getter in (lambda: self._dev.get_feature_report(1, 20),
                       lambda: self._dev.get_input_report(1, 20)):
            try:
                raw = bytes(getter())
                text = raw.split(b"\x00")[0].decode("ascii", "ignore").strip()
                text = "".join(c for c in text if c.isprintable())
                if len(text) >= 3:
                    return text
            except (OSError, ValueError):
                continue
        return None

    def _lost(self, why: Exception) -> None:
        if self._lost_reported:
            return
        self._lost_reported = True
        log.warning("N4 lost: %s", why)
        self._alive = False
        with self._cond:
            self._cond.notify_all()
        self._on_lost()

    def _reader(self) -> None:
        while self._alive:
            try:
                data = self._dev.read(protocol.INPUT_REPORT_SIZE, 250)
            except (OSError, ValueError) as exc:
                if self._alive:
                    self._lost(exc)
                return
            if not data:
                continue
            parsed = protocol.parse_input(data)
            if parsed is None:
                continue
            event = self.model.decode(*parsed)
            if event is None:
                log.debug("unmapped input %02x/%02x", *parsed)
                continue
            try:
                self._on_input(event)
            except Exception:  # noqa: BLE001 - a bad handler must never kill the reader
                log.exception("input handler failed")

    def _writer(self) -> None:
        last_beat = time.monotonic()              # __init__ just sent one
        while self._alive:
            with self._cond:
                if not self._pending and not self._pending_cmds:
                    self._cond.wait(timeout=1.0)
                images, self._pending = self._pending, {}
                cmds, self._pending_cmds = self._pending_cmds, []
            try:
                for report in cmds:
                    self._send(report)
                if images:
                    for image_id, jpeg in images.items():
                        for report in protocol.image(image_id, jpeg):
                            self._send(report)
                    self._send(protocol.flush())
                now = time.monotonic()
                if now - last_beat > HEARTBEAT_SECONDS:
                    self._send(protocol.heartbeat())
                    last_beat = now
            except (OSError, ValueError) as exc:
                if self._alive:
                    self._lost(exc)
                return


class DeviceWatcher:
    """Keeps one N4 connected: opens the first one found and reopens it after unplug/replug."""

    def __init__(self, on_connect: Callable[[N4Device], None], on_disconnect: Callable[[], None],
                 on_input: Callable[[InputEvent], None], serial: str | None = None):
        self._on_connect = on_connect
        self._on_disconnect = on_disconnect
        self._on_input = on_input
        self._serial = serial
        self.device: N4Device | None = None
        self._stop = threading.Event()
        self._lost = threading.Event()
        self._thread = threading.Thread(target=self._run, name="n4-hotplug", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self.device:
            self.device.close()
            self.device = None

    def _run(self) -> None:
        while not self._stop.is_set():
            if self.device is None:
                self._try_open()
            if self._lost.wait(HOTPLUG_SECONDS):
                self._lost.clear()
                if self.device:
                    self.device.close(hand_back=False)
                    self.device = None
                    self._on_disconnect()

    def _try_open(self) -> None:
        for info in enumerate_devices():
            if self._serial and info.serial != self._serial:
                continue
            try:
                self.device = N4Device(info, self._on_input, self._lost.set)
            except OSError as exc:
                log.info("cannot open %s yet: %s", info.label, exc)
                continue
            log.info("connected %s firmware=%s", info.label, self.device.firmware)
            self._on_connect(self.device)
            return
