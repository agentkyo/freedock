"""Euro Truck Simulator 2 / American Truck Simulator: live telemetry + the player's own key bindings.

SCS games have no telemetry export of their own. The community standard is RenCloud's scs-sdk-plugin
(https://github.com/RenCloud/scs-sdk-plugin): ``scs-telemetry.dll`` dropped into
``<game>\\bin\\win_x64\\plugins\\`` publishes the shared memory map ``Local\\SCSTelemetry``.

Offsets below come from ``scs-telemetry/inc/scs-telemetry-common.hpp`` of release **V.1.12.1**
(commit d7216cb16c178be34feb04d9d6f67dd8e71ab8de, PLUGIN_REVID 12). They were computed by mirroring the
``scsTelemetryMap_t`` struct in ctypes (natural MSVC alignment) and cross-checked against the zone
offsets the header documents (zone 2 @40, 3 @500, 4 @700, 5 @1500, 9 @2300, 10 @4000, 12 @4300,
trailers @6000 with 1560 bytes each; total 21600). The mapping is opened read-only with
OpenFileMappingW - FreeDock never creates it.

Key bindings are read from ``controls.sii`` (plain-text SII) in the newest profile.
"""
from __future__ import annotations

import logging
import os
import re
import struct
import sys
import threading
import time
from pathlib import Path

from .assetto import Binding, _Mapping

log = logging.getLogger(__name__)

MMF_NAME = "Local\\SCSTelemetry"
MAP_SIZE = 21600            # sizeof(scsTelemetryMap_t) in plugin rev 12 (the plugin maps 32 KiB)
PLUGIN_REV = 12
PLUGIN_DLL = "scs-telemetry.dll"
GAME_FOLDERS = {"ets2": "Euro Truck Simulator 2", "ats": "American Truck Simulator"}

# --- byte offsets into scsTelemetryMap_t (plugin V.1.12.1) ---------------------------------------
OFF = {
    "sdkActive": 0, "paused": 4, "time": 8, "renderTime": 24,           # bool, bool, u64, u64
    "plugin_rev": 40, "game": 52,                                        # u32 (game: 1 ets2, 2 ats)
    "time_abs": 64,                                                      # u32 in-game minutes
    "retarderStepCount": 76, "deliveryTime": 88, "plannedDistanceKm": 100, "retarderBrake": 108,   # u32
    "restStop": 500, "gearDashboard": 508,                               # s32
    "fuelCapacity": 704, "engineRpmMax": 740,                            # float (config)
    "speed": 948, "engineRpm": 952, "cruiseControlSpeed": 988, "fuel": 1000, "fuelRange": 1008,
    "adblue": 1012, "wearEngine": 1036, "wearTransmission": 1040, "wearCabin": 1044,
    "wearChassis": 1048, "wearWheels": 1052,
    "speedLimit": 1068, "cargoDamage": 1468,                             # float (truck_f / job_f)
    "parkBrake": 1566, "motorBrake": 1567, "airPressureWarning": 1568, "fuelWarning": 1570,
    "adblueWarning": 1571, "electricEnabled": 1575, "engineEnabled": 1576, "wipers": 1577,
    "blinkerLeftActive": 1578, "blinkerRightActive": 1579, "blinkerLeftOn": 1580, "blinkerRightOn": 1581,
    "lightsParking": 1582, "lightsBeamLow": 1583, "lightsBeamHigh": 1584, "lightsBeacon": 1585,
    "lightsHazard": 1588, "cruiseControl": 1589, "differentialLock": 1608, "liftAxle": 1609,
    "trailerLiftAxle": 1611,                                             # bool (truck_b)
    "truckBrand": 2364, "truckName": 2492, "cargo": 2620, "cityDst": 2748, "compDst": 2876,
    "citySrc": 3004, "compSrc": 3132,                                    # char[64]
    "jobIncome": 4000,                                                   # u64
    "onJob": 4300,                                                       # bool (special_b)
    "trailer0_attached": 6080,                                           # trailer[0].com_b.attached
}
_WEAR = ("wearEngine", "wearTransmission", "wearCabin", "wearChassis", "wearWheels")
_DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def _f(b: bytes, key: str) -> float:
    return struct.unpack_from("<f", b, OFF[key])[0]


def _u(b: bytes, key: str) -> int:
    return struct.unpack_from("<I", b, OFF[key])[0]


def _i(b: bytes, key: str) -> int:
    return struct.unpack_from("<i", b, OFF[key])[0]


def _q(b: bytes, key: str) -> int:
    return struct.unpack_from("<Q", b, OFF[key])[0]


def _b(b: bytes, key: str) -> bool:
    return b[OFF[key]] != 0


def _s(b: bytes, key: str) -> str:
    raw = b[OFF[key]:OFF[key] + 64]
    return raw.split(b"\x00", 1)[0].decode("utf-8", "replace").strip()


def fmt_gear(g: int) -> str:
    """SCS dashboard gear: >0 forward, 0 neutral, <0 reverse (R1, R2...)."""
    return "N" if g == 0 else str(g) if g > 0 else f"R{-g}"


def fmt_game_clock(minutes: int | None) -> str | None:
    """In-game time (minutes since Monday 00:00 of week 1) -> ``"Tue 14:05"``."""
    if minutes is None:
        return None
    return f"{_DAYS[(minutes // 1440) % 7]} {minutes % 1440 // 60:02d}:{minutes % 60:02d}"


def decode(b: bytes, stale: bool = False) -> dict:
    """Decode a raw ``scsTelemetryMap_t`` snapshot. Pure function (tests feed it synthetic bytes)."""
    active = _b(b, "sdkActive")
    game = {1: "ets2", 2: "ats"}.get(_u(b, "game"))
    speed_limit = _f(b, "speedLimit")
    planned = _u(b, "plannedDistanceKm")
    on_job = _b(b, "onJob")
    rest = _i(b, "restStop")
    tabs = _u(b, "time_abs")
    job = None
    delivery = _u(b, "deliveryTime")
    if on_job:
        job = {"cargo": _s(b, "cargo"), "source_city": _s(b, "citySrc"), "source_company": _s(b, "compSrc"),
               "dest_city": _s(b, "cityDst"), "dest_company": _s(b, "compDst"), "income": _q(b, "jobIncome"),
               "planned_km": planned or None,
               # game minutes left until the delivery deadline (negative = late); None for no-deadline jobs
               "deadline_min": delivery - tabs if delivery and tabs and delivery < 0xFFFFFFF0 else None}
    return {
        "connected": active and not stale,
        "paused": _b(b, "paused"),
        "game": game,
        "plugin_rev": _u(b, "plugin_rev"),
        "truck": f"{_s(b, 'truckBrand')} {_s(b, 'truckName')}".strip(),
        "speed_kmh": _f(b, "speed") * 3.6,
        "cruise_kmh": _f(b, "cruiseControlSpeed") * 3.6,
        "cruise_on": _b(b, "cruiseControl"),
        "rpm": _f(b, "engineRpm"), "max_rpm": _f(b, "engineRpmMax"),
        "gear": fmt_gear(_i(b, "gearDashboard")),
        "fuel_l": _f(b, "fuel"), "fuel_cap_l": _f(b, "fuelCapacity"), "fuel_range_km": _f(b, "fuelRange"),
        "fuel_warning": _b(b, "fuelWarning"),
        "adblue": _f(b, "adblue"), "adblue_warning": _b(b, "adblueWarning"),
        "air_warning": _b(b, "airPressureWarning"),
        "speed_limit_kmh": speed_limit * 3.6 if speed_limit > 0 else None,
        "parking_brake": _b(b, "parkBrake"), "engine_brake": _b(b, "motorBrake"),
        "engine_on": _b(b, "engineEnabled"), "electric_on": _b(b, "electricEnabled"),
        "lights_parking": _b(b, "lightsParking"), "lights_low": _b(b, "lightsBeamLow"),
        "lights_high": _b(b, "lightsBeamHigh"), "beacon": _b(b, "lightsBeacon"), "hazard": _b(b, "lightsHazard"),
        # *_left/right = indicator lever state; *_lamp = the blinking bulb itself
        "blinker_left": _b(b, "blinkerLeftActive"), "blinker_right": _b(b, "blinkerRightActive"),
        "blinker_left_lamp": _b(b, "blinkerLeftOn"), "blinker_right_lamp": _b(b, "blinkerRightOn"),
        "wipers": _b(b, "wipers"),
        "retarder": _u(b, "retarderBrake"), "retarder_max": _u(b, "retarderStepCount"),
        "diff_lock": _b(b, "differentialLock"), "lift_axle": _b(b, "liftAxle"),
        "trailer_lift_axle": _b(b, "trailerLiftAxle"),
        "trailer_attached": _b(b, "trailer0_attached"),
        "truck_damage": max(_f(b, k) for k in _WEAR),
        "cargo_damage": _f(b, "cargoDamage"),
        "job": job,
        "rest_stop_min": rest if tabs else None,      # time_abs == 0: no profile loaded yet
        "game_time_min": tabs or None,
        "game_clock": fmt_game_clock(tabs) if tabs else None,
        "time": time.time(),
    }


class Ets2Telemetry:
    """Polls ``Local\\SCSTelemetry`` on a background thread; ``data`` is a plain dict snapshot.

    Staleness: the plugin's ``renderTime`` advances every rendered frame (also while paused). If it
    stops for ``STALE_S`` the view is closed and reopened - our handle would otherwise keep the mapping
    of a crashed/closed game alive with ``sdkActive`` still set.
    """

    STALE_S = 2.0

    def __init__(self, hz: float = 20.0):
        self.data: dict = {"connected": False}
        self._hz = hz
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.users = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        if os.environ.get("FREEDOCK_DEMO"):
            self._thread = threading.Thread(target=self._demo, name="ets2-demo", daemon=True)
        elif sys.platform == "win32":
            self._thread = threading.Thread(target=self._run, name="ets2", daemon=True)
        else:
            return
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _demo(self) -> None:
        """Fake but plausible telemetry (FREEDOCK_DEMO=1)."""
        import math
        t0 = time.time()
        while not self._stop.is_set():
            t = time.time() - t0
            speed = 80 + 5 * math.sin(t / 7)
            gm = 8 * 60 + 14 + int(t / 3)
            self.data = {
                "connected": True, "paused": False, "game": "ets2", "plugin_rev": PLUGIN_REV,
                "truck": "Scania S", "speed_kmh": speed, "cruise_kmh": 80.0, "cruise_on": True,
                "rpm": 1150 + 60 * math.sin(t / 3), "max_rpm": 2500.0, "gear": "12",
                "fuel_l": 612.0 - t * 0.05, "fuel_cap_l": 900.0, "fuel_range_km": 1890.0, "fuel_warning": False,
                "adblue": 61.0, "adblue_warning": False, "air_warning": False, "speed_limit_kmh": 90.0,
                "parking_brake": False, "engine_on": True, "electric_on": True,
                "lights_parking": True, "lights_low": True, "lights_high": False, "beacon": False, "hazard": False,
                "blinker_left": int(t) % 20 < 3, "blinker_right": False,
                "blinker_left_lamp": int(t) % 20 < 3 and int(t * 2) % 2 == 0, "blinker_right_lamp": False,
                "wipers": False, "retarder": 0, "retarder_max": 4, "diff_lock": False, "lift_axle": False,
                "trailer_lift_axle": False, "trailer_attached": True, "truck_damage": 0.03, "cargo_damage": 0.0,
                "job": {"cargo": "Tractors", "source_city": "Berlin", "source_company": "Agrominta",
                        "dest_city": "Praha", "dest_company": "Tradeaux", "income": 18450, "planned_km": 352,
                        "deadline_min": 410 - int(t / 3)},
                "engine_brake": int(t) % 10 < 4,
                "rest_stop_min": 365, "game_time_min": gm, "game_clock": fmt_game_clock(gm), "time": time.time(),
            }
            self._stop.wait(1 / 20)

    def _run(self) -> None:
        view = None
        last_rt, last_change = None, time.monotonic()
        while not self._stop.is_set():
            if view is None:
                try:
                    view = _Mapping(MMF_NAME, MAP_SIZE)     # last_rt survives reopen: still stale
                except OSError:
                    self.data = {"connected": False}
                    self._stop.wait(2.0)
                    continue
            try:
                buf = view.read()
            except OSError as exc:
                log.info("SCS telemetry read failed: %s", exc)
                view.close()
                view = None
                continue
            rt = struct.unpack_from("<Q", buf, OFF["renderTime"])[0]
            now = time.monotonic()
            if rt != last_rt:
                last_rt, last_change = rt, now
            stale = now - last_change > self.STALE_S
            try:
                self.data = decode(buf, stale)
            except struct.error as exc:
                log.info("SCS telemetry decode failed: %s", exc)
            if stale:                       # drop our handle so a dead game's mapping can disappear
                view.close()
                view = None
                self._stop.wait(1.0)
                continue
            self._stop.wait(1.0 / self._hz)
        if view:
            view.close()


telemetry = Ets2Telemetry()


# --- game / plugin discovery --------------------------------------------------------------------

def steam_libraries() -> list[Path]:
    """Steam library roots: SteamPath from HKCU\\Software\\Valve\\Steam + steamapps/libraryfolders.vdf."""
    roots: list[Path] = []
    steam = None
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
                steam = Path(winreg.QueryValueEx(k, "SteamPath")[0])
        except OSError:
            pass
    if steam is None:
        for guess in (Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam",
                      Path.home() / ".steam" / "steam", Path.home() / ".local" / "share" / "Steam"):
            if guess.is_dir():
                steam = guess
                break
    if steam is None:
        return roots
    roots.append(steam)
    try:
        vdf = (steam / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
    except OSError:
        vdf = ""
    for m in re.finditer(r'"path"\s+"([^"]+)"', vdf):
        p = Path(m.group(1).replace("\\\\", "\\"))
        if all(os.path.normcase(str(p)) != os.path.normcase(str(r)) for r in roots):
            roots.append(p)
    return roots


def find_game_dirs() -> list[Path]:
    """Installed ETS2/ATS folders across all Steam libraries."""
    out = []
    for lib in steam_libraries():
        for folder in GAME_FOLDERS.values():
            d = lib / "steamapps" / "common" / folder
            if d.is_dir():
                out.append(d)
    return out


def plugin_installed(game_dir: Path | str) -> bool:
    """True if a scs-sdk-plugin DLL sits in ``<game>\\bin\\win_x64\\plugins``."""
    plugins = Path(game_dir) / "bin" / "win_x64" / "plugins"
    try:
        return any(p.suffix.lower() == ".dll" and "scs-telemetry" in p.name.lower() for p in plugins.iterdir())
    except OSError:
        return False


# --- key bindings ---------------------------------------------------------------------------------

# Real SCS action ids (from controls.sii of ETS2 1.5x) -> friendly name, in display order.
ACTIONS = {
    "engine": "Engine start/stop", "engineelect": "Electricity on/off",
    "parkingbrake": "Parking brake", "motorbrake": "Engine brake", "engbraketog": "Engine brake toggle",
    "retarderup": "Retarder +", "retarderdown": "Retarder -",
    "light": "Lights (cycle)", "lightoff": "Lights off", "lightpark": "Parking lights", "lighton": "Low beam",
    "hblight": "High beam", "lighthorn": "Flash lights", "beacon": "Beacon", "flasher4way": "Hazards",
    "lblinker": "Blinker left", "rblinker": "Blinker right",
    "horn": "Horn", "airhorn": "Air horn",
    "wipers": "Wipers", "wipersback": "Wipers back",
    "cruiectrl": "Cruise control", "cruiectrlres": "Cruise resume",
    "cruiectrlinc": "Cruise +", "cruiectrldec": "Cruise -", "accmode": "Adaptive cruise mode",
    "laneassist": "Lane assist",
    "diflock": "Diff lock", "liftaxle": "Lift axle", "liftaxlet": "Lift trailer axle",
    "attach": "Trailer attach/detach", "quickpark": "Quick park",
    "entrdoors": "Coach doors", "parking_cams": "Parking cameras", "cabinlight": "Cabin light",
    "advmouse": "Route advisor", "advzoomout": "Route advisor zoom", "navmap": "Map",
    "display": "Dashboard display", "infotainment": "Infotainment", "showhud": "Toggle HUD",
    "showmirrors": "Toggle mirrors",
    "photo_mode": "Photo mode", "screenshot": "Screenshot",
    "radio": "Radio", "radionext": "Radio next", "radioprev": "Radio previous",
    "radioup": "Radio volume +", "radiodown": "Radio volume -",
    "cam1": "Camera 1 (interior)", "cam2": "Camera 2 (chase)", "cam3": "Camera 3 (top)",
    "cam4": "Camera 4", "cam5": "Camera 5 (bumper)", "cam6": "Camera 6 (wheel)",
    "cam7": "Camera 7 (cinematic)", "cam8": "Camera 8 (TV)", "camcycle": "Camera cycle",
    "lookleft": "Look left", "lookright": "Look right",
    "lwinopen": "Left window down", "lwinclose": "Left window up",
    "rwinopen": "Right window down", "rwinclose": "Right window up",
    "adjuster": "Truck adjuster", "gar_man": "Garage manager", "quicksave": "Quick save",
    "pause": "Pause", "menu": "Menu",
}

# Stock ETS2 keyboard defaults (as written by the game into a fresh controls.sii). Actions that are
# unbound on keyboard out of the box (electricity, windows, cruise +/-, radio volume...) are absent.
DEFAULT_KEYS = {
    "engine": ["e"], "parkingbrake": ["space"], "motorbrake": ["b"],
    "retarderup": ["semicolon"], "retarderdown": ["quote"],
    "light": ["l"], "hblight": ["k"], "lighthorn": ["j"], "beacon": ["o"], "flasher4way": ["f"],
    "lblinker": ["bracketleft"], "rblinker": ["bracketright"], "horn": ["h"], "airhorn": ["n"],
    "wipers": ["p"], "cruiectrl": ["c"], "diflock": ["v"], "liftaxle": ["u"], "attach": ["t"],
    "quickpark": ["q"], "advmouse": ["f1"], "advzoomout": ["f5"], "navmap": ["m"], "display": ["i"],
    "showhud": ["f3"], "showmirrors": ["f2"], "photo_mode": ["equal"], "screenshot": ["f10"],
    "radio": ["r"], "radionext": ["pagedown"], "radioprev": ["pageup"],
    **{f"cam{i}": [str(i)] for i in range(1, 9)}, "camcycle": ["9"],
    "lookleft": ["numdiv"], "lookright": ["nummul"], "adjuster": ["f4"], "gar_man": ["g"],
    "quicksave": ["scrolllock"], "pause": ["pause"], "menu": ["esc"],
}

# SCS key names (keyboard.<name>) -> FreeDock key names
_SCS_KEYS = {
    "space": "space", "enter": "enter", "esc": "esc", "tab": "tab", "backspace": "backspace",
    "caps": "capslock", "numlock": "numlock", "scrollock": "scrolllock", "pause": "pause",
    "del": "delete", "ins": "insert", "home": "home", "end": "end", "pgup": "pageup", "pgdn": "pagedown",
    "uarrow": "up", "darrow": "down", "larrow": "left", "rarrow": "right",
    "lbracket": "bracketleft", "rbracket": "bracketright", "semicolon": "semicolon", "apostrophe": "quote",
    "comma": "comma", "period": "period", "slash": "slash", "backslash": "backslash", "minus": "minus",
    "equal": "equal", "grave": "grave", "sysrq": "printscreen",
    "numplus": "numadd", "numminus": "numsub", "nummultiply": "nummul", "numslash": "numdiv",
    "numperiod": "numdec", "numenter": "numenter",
    "lshift": "shift", "rshift": "rshift", "lctrl": "ctrl", "rctrl": "rctrl", "lalt": "alt", "ralt": "ralt",
    "lcmd": "win", "rcmd": "win",
}
_SCS_KEYS.update({chr(c): chr(c) for c in range(ord("a"), ord("z") + 1)})
_SCS_KEYS.update({f"key{i}": str(i) for i in range(10)})
_SCS_KEYS.update({f"num{i}": f"num{i}" for i in range(10)})
_SCS_KEYS.update({f"f{i}": f"f{i}" for i in range(1, 25)})

# modifier(<name>, ...) groups defined at the top of controls.sii
_SCS_MODS = {"no_modifier": [], "shift_only": ["shift"], "ctrl_only": ["ctrl"], "alt_only": ["alt"],
             "cmd_only": ["win"], "ctr_shf": ["ctrl", "shift"], "shf_alt": ["shift", "alt"],
             "ctr_alt": ["ctrl", "alt"], "ctr_shf_alt": ["ctrl", "shift", "alt"]}
_MOD_ORDER = ("ctrl", "rctrl", "shift", "rshift", "alt", "ralt", "win")

_MIX_RE = re.compile(r'^\s*config_lines\[\d+\]\s*:\s*"mix\s+(\w+)\s+`(.*)`"\s*$')
_KEY_RE = re.compile(r"^keyboard\.(\w+)\?0$")
_MODIFIER_RE = re.compile(r"^modifier\(\s*(\w+)(?:\?0)?\s*,\s*(.+)\)$")


def _split_top(expr: str, seps: str) -> list[str]:
    """Split on any char in ``seps`` that is outside parentheses (``||`` yields empty parts)."""
    parts, depth, buf = [], 0, ""
    for ch in expr:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch in seps and depth == 0:
            parts.append(buf.strip())
            buf = ""
        else:
            buf += ch
    parts.append(buf.strip())
    return [p for p in parts if p]


def _key_combo(term: str) -> list[str] | None:
    """``keyboard.a?0`` or ``keyboard.lalt?0 & keyboard.d?0`` -> FreeDock combo, else None."""
    keys = []
    for part in _split_top(term, "&"):
        m = _KEY_RE.match(part)
        if not m or m.group(1) not in _SCS_KEYS:
            return None
        keys.append(_SCS_KEYS[m.group(1)])
    if not keys:
        return None
    mods = [k for k in _MOD_ORDER if k in keys]
    rest = [k for k in keys if k not in _MOD_ORDER]
    return mods + rest if rest else keys


def parse_mix(expr: str) -> list[str] | None:
    """First keyboard alternative of a ``mix`` expression as a FreeDock combo (None if keyboard-unbound).

    Handles ``keyboard.x?0``, ``modifier(ctrl_only, keyboard.x?0)`` and ``keyboard.a?0 & keyboard.b?0``;
    joystick/mouse/semantical terms, custom (joystick) modifiers and long/short presses are skipped.
    """
    for alt in _split_top(expr, "|"):
        m = _MODIFIER_RE.match(alt)
        mods: list[str] = []
        if m:
            if m.group(1) not in _SCS_MODS:
                continue
            mods, alt = _SCS_MODS[m.group(1)], m.group(2).strip()
        combo = _key_combo(alt)
        if combo:
            return mods + [k for k in combo if k not in mods]
    return None


def parse_controls(text: str) -> dict[str, list[str]]:
    """All keyboard-bound ``mix`` actions of a controls.sii text -> {action id: combo}."""
    out = {}
    for line in text.splitlines():
        m = _MIX_RE.match(line)
        if m and (combo := parse_mix(m.group(2))):
            out[m.group(1)] = combo
    return out


def _documents() -> Path:
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                return Path(os.path.expandvars(winreg.QueryValueEx(k, "Personal")[0]))
        except OSError:
            pass
    return Path(os.environ.get("USERPROFILE", Path.home())) / "Documents"


def controls_path(override: str = "", game: str | None = None) -> Path | None:
    """The most recently modified controls.sii of an ETS2/ATS profile (local or Steam Cloud).

    ``game`` ("ets2" / "ats", from telemetry) keeps yesterday's ATS bindings out of today's ETS2."""
    if override:
        return Path(override)
    docs = _documents()
    found = []
    folders = [GAME_FOLDERS[game]] if game in GAME_FOLDERS else list(GAME_FOLDERS.values())
    for folder in folders:
        for sub in ("steam_profiles", "profiles"):
            found += (docs / folder / sub).glob("*/controls.sii")
    if not found:
        return None
    return max(found, key=lambda p: p.stat().st_mtime)


def read_bindings(path: Path | str | None = None) -> dict[str, Binding]:
    """Keyboard bindings of ``controls.sii`` merged over DEFAULT_KEYS (defaults only if unreadable).

    An action the player explicitly cleared in the file stays unbound (it is not refilled from defaults).
    """
    out = {k: Binding(k, list(v)) for k, v in DEFAULT_KEYS.items()}
    if path is None:
        return out
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.info("cannot read %s: %s", path, exc)
        return out
    if text.startswith("ScsC") or "\x00" in text[:64]:
        log.info("%s is encrypted/binary SII; using default bindings", path)
        return out
    present = {m.group(1) for line in text.splitlines() if (m := _MIX_RE.match(line))}
    for action in present:
        out.pop(action, None)
    for action, combo in parse_controls(text).items():
        out[action] = Binding(action, combo)
    return out
