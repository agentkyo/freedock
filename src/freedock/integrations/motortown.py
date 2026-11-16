"""Motor Town: Behind the Wheel - the game's own UDP telemetry ("Telemetry Data Output").

Turn it on in the game: ESC > Options > Gameplay > Telemetry Data Output, address 127.0.0.1:33330.
The game sends packets only while you drive. Layout (little endian), as decoded by the community
dashboard github.com/LucBre-2009/motor-town-telemetry-dashboard:

    0  "MTTM"   4 major  5 minor  6 u16 payload size (packet = 8 + payload)
    108 f rpm  112 f max rpm  116 i gear (<0 reverse, 0 neutral)  120 i top gear  124 f km/h
    128 f throttle  132 f brake  136 f clutch  140 f steering  144 u8 handbrake
    145 u32 flags: 0x02 in a vehicle, 0x04 reverse, 0x08 lights, 0x10 ABS, 0x20 TCS, 0x40 cruise
    149 f fuel 0..1
    v1.1 (849 bytes) adds 153 u32 flags2, 157 autopilot mode, 158 turn signal, 166 f coolant C,
    190 f cruise target, 194 f limiter target, 202 f fuel litres, 206 f tank, 230 f road speed limit,
    284 i64 money, 753 vehicle name (64 bytes)

The turn signal byte is read as 1 = left, 2 = right, 3 = hazard. No public document says so: check it
in the game (the strip shows it) before relying on it.
"""
from __future__ import annotations

import logging
import os
import struct
import threading
import time

from .udp_share import SHARE_PORTS, SharedPort

log = logging.getLogger(__name__)

PORT = 33330
MAGIC = b"MTTM"
V1_SIZE, V11_SIZE = 153, 849
F_VEHICLE, F_REVERSE, F_LIGHTS, F_ABS, F_TCS, F_CRUISE = 0x02, 0x04, 0x08, 0x10, 0x20, 0x40
SIGNAL = {1: "left", 2: "right", 3: "hazard"}
BLINK_S = 0.36            # the strip blinks the lamp itself (the game sends only which side is on)


def _f(b: bytes, off: int) -> float:
    return struct.unpack_from("<f", b, off)[0]


def _i(b: bytes, off: int) -> int:
    return struct.unpack_from("<i", b, off)[0]


def fmt_gear(gear: int, reverse: bool) -> str:
    return "R" if reverse or gear < 0 else "N" if gear == 0 else str(gear)


def decode_packet(pkt: bytes) -> dict | None:
    """One datagram -> dict, or None if it is not a Motor Town v1 packet."""
    if len(pkt) < V1_SIZE or pkt[:4] != MAGIC or pkt[4] != 1:
        return None
    size = struct.unpack_from("<H", pkt, 6)[0]
    if len(pkt) != 8 + size:
        return None
    flags = struct.unpack_from("<I", pkt, 145)[0]
    reverse = bool(flags & F_REVERSE)
    gear = _i(pkt, 116)
    d = {
        "connected": True, "in_vehicle": bool(flags & F_VEHICLE),
        "rpm": _f(pkt, 108), "max_rpm": _f(pkt, 112), "gear": fmt_gear(gear, reverse), "top_gear": _i(pkt, 120),
        "speed_kmh": _f(pkt, 124), "throttle": _f(pkt, 128), "brake": _f(pkt, 132), "clutch": _f(pkt, 136),
        "steering": _f(pkt, 140), "handbrake": pkt[144] != 0, "reverse": reverse,
        "lights": bool(flags & F_LIGHTS), "abs": bool(flags & F_ABS), "tc": bool(flags & F_TCS),
        "cruise": bool(flags & F_CRUISE), "fuel": max(0.0, min(1.0, _f(pkt, 149))),
        "signal": None, "time": time.time(),
    }
    if len(pkt) >= V11_SIZE:
        d.update({
            "signal": SIGNAL.get(pkt[158]), "coolant_c": _f(pkt, 166), "cruise_kmh": _f(pkt, 190),
            "limiter_kmh": _f(pkt, 194), "fuel_l": _f(pkt, 202), "tank_l": _f(pkt, 206),
            "road_limit_kmh": _f(pkt, 230), "money": struct.unpack_from("<q", pkt, 284)[0],
            "vehicle": pkt[753:817].split(b"\0", 1)[0].decode("utf-8", "replace"),
        })
    d["signal_left"] = d["signal"] in ("left", "hazard")
    d["signal_right"] = d["signal"] in ("right", "hazard")
    d["hazard"] = d["signal"] == "hazard"
    return d


def strip_signals(d: dict, now: float) -> dict:
    """What Ambibox LED paints over the ambilight ({"cmd": "signals"}): the lamps, already blinking."""
    lit = int(now / BLINK_S) % 2 == 0
    return {"left": bool(d.get("signal_left")) and lit, "right": bool(d.get("signal_right")) and lit,
            "high_beam": False, "brake": (d.get("brake") or 0) > 0.15, "reverse": bool(d.get("reverse"))}


class MotorTownTelemetry:
    """Listens on UDP 127.0.0.1:33330 on a background thread; ``data`` is a dict snapshot.

    ``connected`` stays True while packets arrived in the last ``TIMEOUT_S`` seconds (the game sends
    only while you drive, so a parked truck reads as disconnected after a moment).
    """

    TIMEOUT_S = 3.0

    def __init__(self, port: int = PORT, host: str = "127.0.0.1"):
        self.data: dict = {"connected": False}
        self.port, self.host = port, host
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        demo = bool(os.environ.get("FREEDOCK_DEMO"))
        self._thread = threading.Thread(target=self._demo if demo else self._run,
                                        name="motortown-demo" if demo else "motortown", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _demo(self) -> None:
        t0 = time.time()
        while not self._stop.is_set():
            t = time.time() - t0
            cyc = (t % 5.0) / 5.0
            gear = 1 + int(t / 5.0) % 5
            sig = "left" if int(t) % 12 < 4 else None
            self.data = {"connected": True, "in_vehicle": True, "rpm": 900 + 1700 * cyc, "max_rpm": 2600.0,
                         "gear": str(gear), "top_gear": 12, "speed_kmh": 20 + gear * 12 + 8 * cyc, "throttle": 0.8,
                         "brake": 0.0, "clutch": 0.0, "steering": 0.0, "handbrake": False, "reverse": False,
                         "lights": True, "abs": False, "tc": False, "cruise": False, "fuel": 0.64,
                         "signal": sig, "signal_left": sig == "left", "signal_right": False, "hazard": False,
                         "coolant_c": 88.0, "cruise_kmh": 0.0, "limiter_kmh": 0.0, "fuel_l": 260.0,
                         "tank_l": 400.0, "road_limit_kmh": 80.0, "money": 152_300, "vehicle": "Demo truck",
                         "time": time.time()}
            self._stop.wait(1 / 30)

    def _run(self) -> None:
        # the port is shared with other apps (PitGlass): see udp_share
        feed = SharedPort(self.port, SHARE_PORTS.get(self.port), self.host)
        last = 0.0
        while not self._stop.is_set():
            pkt = feed.recv(4096)
            d = decode_packet(pkt) if pkt else None
            if d is not None:
                self.data, last = d, time.monotonic()
            elif self.data.get("connected") and time.monotonic() - last > self.TIMEOUT_S:
                self.data = {"connected": False}
        feed.close()


telemetry = MotorTownTelemetry()
