"""Assetto Corsa: live telemetry from shared memory + firing the player's own key bindings.

Shared memory is read by explicit offsets (validated against a real session): the C++ structs mix
wchar_t arrays aligned to 2 with ints aligned to 4, which inserts padding in non-obvious places.
The mapping is opened with OpenFileMappingW so FreeDock never *creates* the AC mapping itself.
"""
from __future__ import annotations

import configparser
import ctypes
import logging
import os
import struct
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

PHYSICS_SIZE, GRAPHICS_SIZE, STATIC_SIZE = 800, 1600, 820

# AC hard-codes Ctrl for these bindings; controls.ini only stores the letter.
CTRL_IMPLIED = {"ABS", "TRACTION_CONTROL", "AUTO_SHIFTER", "ACTIVATE_AI", "IDEAL_LINE", "MOUSE_STEERING",
                "HIDE_APPS", "DRIVER_NAMES", "HIDE_DAMAGE", "SHOW_DAMAGE"}

FRIENDLY = {
    "ACTION_HEADLIGHTS": "Headlights", "ACTION_HEADLIGHTS_FLASH": "Flash lights", "ACTION_HORN": "Horn",
    "__EXT_HAZARDS": "Hazards", "__EXT_TURNSIGNAL_LEFT": "Turn left", "__EXT_TURNSIGNAL_RIGHT": "Turn right",
    "__EXT_WIPERS_MORE": "Wipers +", "__EXT_WIPERS_LESS": "Wipers -", "__EXT_WIPERS_OFF": "Wipers off",
    "ABS": "ABS", "TRACTION_CONTROL": "TC", "TCUP": "TC +", "TCDN": "TC -", "ABSUP": "ABS +", "ABSDN": "ABS -",
    "BALANCEUP": "Brake bias +", "BALANCEDN": "Brake bias -", "__CM_ENGINE_BRAKE": "Engine brake",
    "__EXT_ENGINEMAP_UP": "Engine map +", "__EXT_ENGINEMAP_DOWN": "Engine map -", "DRS": "DRS", "KERS": "KERS",
    "HIDE_APPS": "Hide apps", "__EXT_HIDE_UI": "Hide UI", "__EXT_SAVE_CLIP": "Save replay clip",
    "__EXT_SCREENSHOT_ALT": "Screenshot", "__EXT_VIRTUAL_MIRROR": "Virtual mirror", "GEARUP": "Gear up",
    "GEARDN": "Gear down", "PIT_LIMITER": "Pit limiter", "__CM_RESET_CAMERA_VR": "Reset VR camera",
    "IDEAL_LINE": "Ideal line", "AUTO_SHIFTER": "Auto shifter", "GLANCEBACK": "Look back",
}


def controls_ini_path(override: str = "") -> Path:
    if override:
        return Path(override)
    docs = Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"
    return docs / "Assetto Corsa" / "cfg" / "controls.ini"


@dataclass(frozen=True)
class Binding:
    section: str
    combo: list[str]          # FreeDock key names (vk:0x.. for raw codes)

    @property
    def text(self) -> str:
        return "+".join(k.replace("vk:", "") for k in self.combo)


_MOD_VK = {16: "shift", 17: "ctrl", 18: "alt", 0x10: "shift", 0xA0: "shift", 0xA2: "ctrl", 0xA4: "alt"}


def read_bindings(path: Path) -> dict[str, Binding]:
    """Keyboard bindings from Assetto Corsa's controls.ini (sections with a KEY)."""
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8-sig")
    except (OSError, configparser.Error) as exc:
        log.info("cannot read %s: %s", path, exc)
        return {}
    out = {}
    for section in cp.sections():
        raw = cp.get(section, "KEY", fallback="-1").strip()
        try:
            vk = int(raw, 0)
        except ValueError:
            continue
        if vk <= 0:
            continue
        combo: list[str] = []
        if section in CTRL_IMPLIED:
            combo.append("ctrl")
        for m in cp.get(section, "KEY_MODIFICATOR", fallback="").replace(";", ",").split(","):
            m = m.strip()
            if m.lstrip("-").isdigit() and int(m) > 0 and (name := _MOD_VK.get(int(m))) and name not in combo:
                combo.append(name)
        combo.append(f"vk:{vk:#04x}")
        out[section] = Binding(section, combo)
    return out


class _Mapping:
    def __init__(self, name: str, size: int):
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.OpenFileMappingW.restype = ctypes.c_void_p
        k32.OpenFileMappingW.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_wchar_p)
        k32.MapViewOfFile.restype = ctypes.c_void_p
        k32.MapViewOfFile.argtypes = (ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_size_t)
        k32.UnmapViewOfFile.argtypes = (ctypes.c_void_p,)
        k32.CloseHandle.argtypes = (ctypes.c_void_p,)
        self._k32 = k32
        self.handle = k32.OpenFileMappingW(0x0004, 0, name)   # FILE_MAP_READ
        if not self.handle:
            raise OSError("not running")
        self.view = k32.MapViewOfFile(self.handle, 0x0004, 0, 0, size)
        if not self.view:
            k32.CloseHandle(self.handle)
            raise OSError("map failed")
        self.size = size

    def read(self) -> bytes:
        return ctypes.string_at(self.view, self.size)

    def close(self) -> None:
        self._k32.UnmapViewOfFile(self.view)
        self._k32.CloseHandle(self.handle)


def _f(b: bytes, off: int) -> float:
    return struct.unpack_from("<f", b, off)[0]


def _i(b: bytes, off: int) -> int:
    return struct.unpack_from("<i", b, off)[0]


def _w(b: bytes, off: int, chars: int) -> str:
    return b[off:off + chars * 2].decode("utf-16-le", "ignore").split("\x00")[0].strip()


def fmt_lap(ms: int) -> str:
    if ms <= 0 or ms >= 999_999_999:
        return "-:--.---"
    return f"{ms // 60000}:{ms % 60000 // 1000:02d}.{ms % 1000:03d}"


class AssettoTelemetry:
    """Polls AC shared memory on a background thread; ``data`` is a plain dict snapshot."""

    STATUS = {0: "off", 1: "replay", 2: "live", 3: "pause"}

    def __init__(self, hz: float = 30.0):
        self.data: dict = {"connected": False}
        self._hz = hz
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.users = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if os.environ.get("FREEDOCK_DEMO"):
            self._stop.clear()
            self._thread = threading.Thread(target=self._demo, name="assetto-demo", daemon=True)
            self._thread.start()
            return
        if sys.platform != "win32":
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="assetto", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _demo(self) -> None:
        """Fake but plausible telemetry (FREEDOCK_DEMO=1) - for screenshots and trying animations."""
        import math
        t0 = time.time()
        best = 98_412
        while not self._stop.is_set():
            t = time.time() - t0
            cyc = (t % 6.0) / 6.0                     # one gear pull every 6 s
            gear = 2 + int(t / 6.0) % 4
            rpm = int(4200 + 4100 * cyc ** 1.4)
            lap_ms = int((t * 1000 + 61_000) % 101_000)
            self.data = {"connected": True, "status": "live", "car": "ks_porsche_911_gt3_r_2016",
                         "track": "imola", "gas": 1.0, "brake": 0.0, "fuel": 41.3 - t * 0.01, "max_fuel": 120.0,
                         "gear": str(gear), "rpm": rpm, "max_rpm": 8500, "speed": 92 + gear * 31 + 30 * cyc,
                         "tc": 0.0, "abs": 0.0, "pit_limiter": False, "brake_bias": 0.565 + 0.01 * math.sin(t / 9),
                         "air_temp": 18.0, "road_temp": 27.0, "tyre_core": [82.0, 84.0, 79.0, 80.0],
                         "laps": 4 + int(t // 101), "position": 3, "current_ms": lap_ms, "last_ms": best,
                         "best_ms": best, "in_pit": False, "sector": int(lap_ms / 34_000), "total_laps": 12,
                         "time": time.time()}
            self._stop.wait(1 / 30)

    def _run(self) -> None:
        maps = None
        while not self._stop.is_set():
            if maps is None:
                try:
                    maps = (_Mapping("Local\\acpmf_physics", PHYSICS_SIZE),
                            _Mapping("Local\\acpmf_graphics", GRAPHICS_SIZE),
                            _Mapping("Local\\acpmf_static", STATIC_SIZE))
                except OSError:
                    self.data = {"connected": False}
                    self._stop.wait(2.0)
                    continue
            try:
                self.data = self._decode(*(m.read() for m in maps))
            except (OSError, struct.error) as exc:
                log.info("AC read failed: %s", exc)
                for m in maps:
                    m.close()
                maps = None
            self._stop.wait(1.0 / self._hz)
        if maps:
            for m in maps:
                m.close()

    def _decode(self, ph: bytes, gf: bytes, st: bytes) -> dict:
        status = self.STATUS.get(_i(gf, 4), "off")
        max_rpm = _i(st, 412)
        max_fuel = _f(st, 416)
        gear_raw = _i(ph, 16)
        gear = "R" if gear_raw == 0 else "N" if gear_raw == 1 else str(gear_raw - 1)
        return {
            "connected": status != "off" and 1000 < max_rpm < 30000,
            "status": status,
            "car": _w(st, 68, 33), "track": _w(st, 134, 33),
            "gas": _f(ph, 4), "brake": _f(ph, 8), "fuel": _f(ph, 12), "max_fuel": max_fuel,
            "gear": gear, "rpm": _i(ph, 20), "max_rpm": max_rpm, "speed": _f(ph, 28),
            "tc": _f(ph, 204), "abs": _f(ph, 252), "pit_limiter": _i(ph, 248) != 0,
            "brake_bias": _f(ph, 564), "air_temp": _f(ph, 288), "road_temp": _f(ph, 292),
            "tyre_core": [_f(ph, 152 + 4 * i) for i in range(4)],
            "laps": _i(gf, 132), "position": _i(gf, 136), "current_ms": _i(gf, 140),
            "last_ms": _i(gf, 144), "best_ms": _i(gf, 148), "in_pit": _i(gf, 160) != 0,
            "sector": _i(gf, 164), "total_laps": _i(gf, 172), "time": time.time(),
        }


telemetry = AssettoTelemetry()
