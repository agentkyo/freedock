"""BeamNG.drive: OutGauge UDP telemetry, OutGauge settings helper, and the player's own key bindings.

OutGauge is BeamNG's built-in port of the Live for Speed packet (``lua/vehicle/protocols/outgauge.lua``,
verified against game v0.40.4). BeamNG fills: car="beam", flags (OG_KM|OG_BAR|OG_TURBO), gear
(0=R, 1=N, 2=1st.. = gearIndex+1, so it can go negative for R2), speed (wheel speed, m/s), rpm, turbo
(bar), engTemp (water), fuel 0..1, oilTemp, throttle, brake, clutch and the dash light bits below.
Hard-coded/unused by BeamNG: time=0, plid=0, oilPressure=0, display1/2="", id=0, DL_PITSPEED and
DL_SIGNAL_ANY never set; DL_BATTERY means "engine not running"; DL_TC means ESC/TCS intervening.

Settings: ``protocols_outgauge_{enabled,address,port}`` are "cloud" settings (defaults.json), stored
in ``<user>/settings/cloud/settings.json``; ``settings/settings.json`` (local) is merged over it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import struct
import threading
import time
from pathlib import Path

from .assetto import Binding

log = logging.getLogger(__name__)

DEFAULT_PORT = 4444
GAME_FOLDER = "BeamNG.drive"

# LFS OutGauge packet as declared by BeamNG (little endian, packed): 92 bytes, 96 with the optional id
_PACKET = struct.Struct("<I4sHbB7f2I3f16s16s")
_PACKET_ID = struct.Struct("<i")

DL_SHIFT, DL_FULLBEAM, DL_HANDBRAKE, DL_PITSPEED = 1 << 0, 1 << 1, 1 << 2, 1 << 3
DL_TC, DL_SIGNAL_L, DL_SIGNAL_R, DL_SIGNAL_ANY = 1 << 4, 1 << 5, 1 << 6, 1 << 7
DL_OILWARN, DL_BATTERY, DL_ABS, DL_SPARE = 1 << 8, 1 << 9, 1 << 10, 1 << 11
OG_SHIFT, OG_CTRL, OG_TURBO, OG_KM, OG_BAR = 1, 2, 8192, 16384, 32768


def fmt_gear(g: int) -> str:
    """OutGauge gear byte: 0 = R, 1 = N, 2 = 1st... (BeamNG may send -1 for a second reverse)."""
    return "N" if g == 1 else str(g - 1) if g > 1 else "R" if g == 0 else f"R{1 - g}"


def decode_packet(pkt: bytes) -> dict:
    """Decode one OutGauge datagram (92 or 96 bytes). Raises ValueError if it is too short."""
    if len(pkt) < _PACKET.size:
        raise ValueError(f"OutGauge packet too short ({len(pkt)} bytes)")
    (_t, car, flags, gear, _plid, speed, rpm, turbo, eng_temp, fuel, oil_p, oil_t,
     dash, show, throttle, brake, clutch, _d1, _d2) = _PACKET.unpack_from(pkt)

    def on(bit: int) -> bool:
        return bool(show & bit)

    left, right = on(DL_SIGNAL_L), on(DL_SIGNAL_R)
    return {
        "connected": True,
        "car": car.split(b"\x00", 1)[0].decode("ascii", "replace"),
        "flags": flags,
        "speed_kmh": speed * 3.6, "rpm": rpm, "gear": fmt_gear(gear),
        "turbo_bar": turbo, "eng_temp": eng_temp, "oil_temp": oil_t, "oil_pressure": oil_p,
        "fuel": fuel, "throttle": throttle, "brake": brake, "clutch": clutch,
        "shift_light": on(DL_SHIFT), "full_beam": on(DL_FULLBEAM), "handbrake": on(DL_HANDBRAKE),
        "pit_limiter": on(DL_PITSPEED), "tc": on(DL_TC), "abs": on(DL_ABS),
        "signal_left": left, "signal_right": right, "hazard": left and right,
        "oil_warn": on(DL_OILWARN), "battery_warn": on(DL_BATTERY),
        "dash_lights": dash,
        "id": _PACKET_ID.unpack_from(pkt, _PACKET.size)[0] if len(pkt) >= _PACKET.size + 4 else None,
        "time": time.time(),
    }


class BeamNGTelemetry:
    """Listens for OutGauge on UDP ``host:port`` on a background thread; ``data`` is a dict snapshot.

    ``connected`` is True while a packet arrived in the last ``TIMEOUT_S`` seconds. With ``port=None``
    the port configured in BeamNG's settings is used (falls back to 4444).
    """

    TIMEOUT_S = 2.0

    def __init__(self, port: int | None = None, host: str = "127.0.0.1"):
        self.data: dict = {"connected": False}
        self.port = port
        self.host = host
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.users = 0

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        demo = bool(os.environ.get("FREEDOCK_DEMO"))
        self._thread = threading.Thread(target=self._demo if demo else self._run,
                                        name="beamng-demo" if demo else "beamng", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _demo(self) -> None:
        """Fake but plausible telemetry (FREEDOCK_DEMO=1)."""
        import math
        t0 = time.time()
        while not self._stop.is_set():
            t = time.time() - t0
            cyc = (t % 5.0) / 5.0
            gear = 2 + int(t / 5.0) % 4
            sig = int(t) % 12 < 3 and int(t * 2) % 2 == 0
            self.data = {
                "connected": True, "car": "beam", "flags": OG_KM | OG_BAR | OG_TURBO,
                "speed_kmh": 40 + gear * 22 + 20 * cyc, "rpm": 2500 + 4000 * cyc ** 1.3, "gear": str(gear),
                "turbo_bar": 0.9 * cyc, "eng_temp": 92.0 + math.sin(t / 30), "oil_temp": 104.0, "oil_pressure": 0.0,
                "fuel": max(0.0, 0.62 - t * 0.0005), "throttle": 1.0, "brake": 0.0, "clutch": 0.0,
                "shift_light": cyc > 0.9, "full_beam": False, "handbrake": False, "pit_limiter": False,
                "tc": False, "abs": False, "signal_left": sig, "signal_right": False, "hazard": False,
                "oil_warn": False, "battery_warn": False, "dash_lights": 0x777, "id": 0, "time": time.time(),
            }
            self._stop.wait(1 / 30)

    def _run(self) -> None:
        port = self.port or outgauge_status().get("port") or DEFAULT_PORT
        sock = None
        last = 0.0
        while not self._stop.is_set():
            if sock is None:
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    sock.bind((self.host, port))
                    sock.settimeout(0.5)
                except OSError as exc:
                    log.info("cannot listen for OutGauge on %s:%s: %s", self.host, port, exc)
                    if sock:
                        sock.close()
                    sock = None
                    self.data = {"connected": False}
                    self._stop.wait(5.0)
                    continue
            try:
                pkt = sock.recv(512)
                self.data = decode_packet(pkt)
                last = time.monotonic()
            except socket.timeout:
                pass
            except (OSError, ValueError, struct.error) as exc:
                log.debug("OutGauge packet ignored: %s", exc)
            if self.data.get("connected") and time.monotonic() - last > self.TIMEOUT_S:
                self.data = {"connected": False}
        if sock:
            sock.close()


telemetry = BeamNGTelemetry()


# --- files ----------------------------------------------------------------------------------------

def loads_lenient(text: str):
    """BeamNG's JSON flavour: allows // and /* */ comments and trailing commas."""
    out, i, n, in_str = [], 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(c)
            i += 1
    return json.loads(re.sub(r",(\s*[\]}])", r"\1", "".join(out)))


def _read_json(path: Path) -> dict:
    try:
        data = loads_lenient(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        if path.exists():
            log.info("cannot read %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def game_dir(override: str = "") -> Path | None:
    """BeamNG.drive install folder (Steam libraries)."""
    if override:
        return Path(override)
    from .ets2 import steam_libraries
    for lib in steam_libraries():
        d = lib / "steamapps" / "common" / GAME_FOLDER
        if (d / "settings").is_dir():
            return d
    return None


def user_dir(override: str = "") -> Path | None:
    """BeamNG user folder: startup.ini ``UserPath``, else %LOCALAPPDATA%/BeamNG/BeamNG.drive/current."""
    if override:
        return Path(override)
    gd = game_dir()
    if gd:
        try:
            for line in (gd / "startup.ini").read_text(encoding="utf-8", errors="replace").splitlines():
                m = re.match(r"\s*UserPath\s*=\s*(.+?)\s*$", line)
                if m:
                    p = Path(m.group(1))
                    p = p if p.is_absolute() else gd / p
                    if (p / "current").is_dir():
                        return p / "current"
                    if p.is_dir():
                        return p
        except OSError:
            pass
    local = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    for cand in (local / "BeamNG" / "BeamNG.drive" / "current", local / "BeamNG.drive" / "current"):
        if cand.is_dir():
            return cand
    legacy = local / "BeamNG.drive"          # pre-0.32 layout: one folder per version, e.g. 0.31
    versions = sorted((p for p in legacy.glob("[0-9]*.[0-9]*") if p.is_dir()),
                      key=lambda p: [int(x) for x in re.findall(r"\d+", p.name)]) if legacy.is_dir() else []
    return versions[-1] if versions else None


# --- OutGauge settings ----------------------------------------------------------------------------

_OG_KEYS = {"enabled": "protocols_outgauge_enabled", "ip": "protocols_outgauge_address",
            "port": "protocols_outgauge_port", "max_rate": "protocols_outgauge_maxUpdateRate"}
_OG_DEPRECATED = {"outgaugeEnabled": "protocols_outgauge_enabled", "outgaugeIP": "protocols_outgauge_address",
                  "outgaugePort": "protocols_outgauge_port"}
_OG_DEFAULTS = {"protocols_outgauge_enabled": False, "protocols_outgauge_address": "127.0.0.1",
                "protocols_outgauge_port": DEFAULT_PORT, "protocols_outgauge_maxUpdateRate": 60}


def _upgrade(values: dict) -> dict:
    out = dict(values)
    for old, new in _OG_DEPRECATED.items():
        if old in out:
            out.setdefault(new, out.pop(old))
    return out


def outgauge_status(udir: Path | str | None = None, gdir: Path | str | None = None) -> dict:
    """Effective OutGauge config: defaults.json < cloud/settings.json < settings.json (like the game).

    Returns ``{"enabled", "ip", "port", "max_rate", "settings_file", "user_dir"}``; ``settings_file`` is
    the cloud settings file ``enable_outgauge`` writes to (None if no user folder was found).
    """
    udir = Path(udir) if udir else user_dir()
    gdir = Path(gdir) if gdir else game_dir()
    values = dict(_OG_DEFAULTS)
    if gdir:
        for k, v in _read_json(gdir / "settings" / "defaults.json").items():
            if k in _OG_DEFAULTS and isinstance(v, list) and len(v) == 2:
                values[k] = v[1]
    if udir:
        for f in (udir / "settings" / "cloud" / "settings.json", udir / "settings" / "settings.json"):
            values.update({k: v for k, v in _upgrade(_read_json(f)).items() if k in _OG_DEFAULTS})
    try:
        port = int(values[_OG_KEYS["port"]])
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return {"enabled": bool(values[_OG_KEYS["enabled"]]), "ip": str(values[_OG_KEYS["ip"]]), "port": port,
            "max_rate": values[_OG_KEYS["max_rate"]],
            "settings_file": udir / "settings" / "cloud" / "settings.json" if udir else None,
            "user_dir": udir}


def _write_json(path: Path, data: dict, backup: bool) -> None:
    if backup and path.exists():
        bak = path.with_name(path.name + f".freedock-{time.strftime('%Y%m%d-%H%M%S')}.bak")
        shutil.copy2(path, bak)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".freedock-tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def enable_outgauge(port: int = DEFAULT_PORT, backup: bool = True, address: str = "127.0.0.1",
                    udir: Path | str | None = None) -> Path:
    """Turn OutGauge on in BeamNG's user settings; returns the cloud settings file that was written.

    Writes ``protocols_outgauge_{enabled,address,port}`` to ``settings/cloud/settings.json`` (with a
    timestamped ``.bak`` copy first when ``backup``). If the local ``settings.json`` carries any of these
    keys (it is merged over cloud) they are updated there too; deprecated ``outgauge*`` keys are dropped.
    Do this while BeamNG is closed: the game caches settings and rewrites both files when it saves.
    """
    udir = Path(udir) if udir else user_dir()
    if udir is None:
        raise FileNotFoundError("BeamNG user folder not found")
    want = {"protocols_outgauge_enabled": True, "protocols_outgauge_address": address,
            "protocols_outgauge_port": int(port)}
    cloud = udir / "settings" / "cloud" / "settings.json"
    data = _read_json(cloud)
    if cloud.exists() and not data and cloud.read_text(encoding="utf-8-sig", errors="replace").strip():
        raise ValueError(f"{cloud} is not valid JSON; refusing to overwrite it")
    for old in _OG_DEPRECATED:
        data.pop(old, None)
    data.update(want)
    _write_json(cloud, data, backup)
    local = udir / "settings" / "settings.json"
    ldata = _read_json(local)
    if any(k in ldata for k in (*want, *_OG_DEPRECATED)):
        for old in _OG_DEPRECATED:
            ldata.pop(old, None)
        ldata.update({k: v for k, v in want.items()})
        _write_json(local, ldata, backup)
    return cloud


# --- key bindings ---------------------------------------------------------------------------------

# Real BeamNG input action ids (lua/ge/extensions/core/input/actions/*.json) -> friendly name.
ACTIONS = {
    "recover_vehicle": "Recover / rewind", "recover_to_last_road": "Recover to road",
    "reset_physics": "Reset vehicle", "reset_all_physics": "Reset all vehicles",
    "reload_vehicle": "Reload vehicle", "loadHome": "Load home", "saveHome": "Save home",
    "activateStarterMotor": "Ignition / starter", "setIgnitionLevel0": "Ignition off",
    "parkingbrake_toggle": "Parking brake (toggle)", "parkingbrake": "Parking brake (hold)",
    "toggle_headlights": "Headlights", "highbeam": "Flash high beam", "toggle_foglights": "Fog lights",
    "toggle_lightbar_signal": "Lightbar", "toggleLightbarMode": "Lightbar mode",
    "toggle_hazard_signal": "Hazards", "toggle_left_signal": "Blinker left", "toggle_right_signal": "Blinker right",
    "horn": "Horn",
    "shiftUp": "Gear up", "shiftDown": "Gear down", "toggleShifterMode": "Shifter mode",
    "toggleESCMode": "ESC / TC mode", "toggleDiffMode": "Diff mode", "toggle4WDStatus": "4WD",
    "toggleRangeStatus": "Range (low/high)", "toggleNitrousOxide": "Nitrous",
    "set_cc_speed": "Cruise set", "reset_cc_speed": "Cruise resume", "increase_cc_speed": "Cruise +",
    "decrease_cc_speed": "Cruise -", "disable_cc": "Cruise off",
    "switch_camera_next": "Next camera", "switch_camera_prev": "Previous camera",
    "toggleCamera": "Free camera", "center_camera": "Center camera", "look_back": "Look back",
    **{f"camera_{i}": f"Camera {i}" for i in range(1, 11)},
    "toggle_slow_motion": "Slow motion", "slower_motion": "Slower motion", "faster_motion": "Faster motion",
    "pause": "Pause physics", "toggleWalkingMode": "Walk / enter vehicle", "gameplay_interact": "Interact",
    "couplersToggle": "Couplers",
    "toggleRadialMenuMulti": "Radial menu", "vehicle_selector": "Vehicle selector", "parts_selector": "Vehicle config",
    "switch_next_vehicle": "Next vehicle", "switch_previous_vehicle": "Previous vehicle",
    "toggleBigMap": "Map", "toggle_minimap": "Minimap", "photomode": "Photo mode",
    "doScreenShot": "Screenshot", "doBigScreenShot": "Big screenshot", "toggleUi": "Toggle UI",
    "toggleReplayRecording": "Replay recording", "toggleTraffic": "Traffic", "toggleMenues": "Menu",
}

# Stock keyboard defaults of the actions above (settings/inputmaps/keyboard.json, game v0.40.4).
DEFAULT_KEYS = {
    "recover_vehicle": ["insert"], "reset_physics": ["r"], "reset_all_physics": ["shift", "r"],
    "reload_vehicle": ["ctrl", "r"], "loadHome": ["home"], "saveHome": ["ctrl", "home"],
    "activateStarterMotor": ["v"], "parkingbrake_toggle": ["p"], "parkingbrake": ["space"],
    "toggle_headlights": ["n"], "toggle_foglights": ["alt", "n"], "toggle_lightbar_signal": ["shift", "n"],
    "toggleLightbarMode": ["shift", "alt", "n"], "toggle_hazard_signal": ["slash"],
    "toggle_left_signal": ["comma"], "toggle_right_signal": ["period"], "horn": ["h"],
    "shiftUp": ["x"], "shiftDown": ["z"], "toggleShifterMode": ["q"], "toggleESCMode": ["ctrl", "q"],
    "toggleDiffMode": ["alt", "d"], "toggle4WDStatus": ["alt", "s"], "toggleRangeStatus": ["alt", "a"],
    "toggleNitrousOxide": ["b"], "switch_camera_next": ["c"], "toggleCamera": ["shift", "c"],
    "center_camera": ["num5"], "look_back": ["num1"],
    **{f"camera_{i}": [str(i % 10)] for i in range(1, 11)},
    "toggle_slow_motion": ["alt", "down"], "slower_motion": ["alt", "left"], "faster_motion": ["alt", "right"],
    "pause": ["j"], "toggleWalkingMode": ["f"], "gameplay_interact": ["shift", "e"], "couplersToggle": ["l"],
    "toggleRadialMenuMulti": ["e"], "vehicle_selector": ["ctrl", "e"], "parts_selector": ["ctrl", "w"],
    "switch_next_vehicle": ["tab"], "switch_previous_vehicle": ["ctrl", "tab"], "toggleBigMap": ["m"],
    "toggle_minimap": ["tab"], "photomode": ["ctrl", "alt", "p"], "doScreenShot": ["ctrl", "p"],
    "doBigScreenShot": ["alt", "p"], "toggleUi": ["alt", "u"], "toggleReplayRecording": ["f2"],
    "toggleTraffic": ["ctrl", "shift", "t"], "toggleMenues": ["esc"],
}

# BeamNG (Torque) keyboard control names -> FreeDock key names
_BNG_KEYS = {
    "escape": "esc", "space": "space", "enter": "enter", "return": "enter", "tab": "tab",
    "backspace": "backspace", "insert": "insert", "delete": "delete", "home": "home", "end": "end",
    "pageup": "pageup", "pagedown": "pagedown", "up": "up", "down": "down", "left": "left", "right": "right",
    "comma": "comma", "period": "period", "slash": "slash", "backslash": "backslash",
    "semicolon": "semicolon", "apostrophe": "quote", "lbracket": "bracketleft", "rbracket": "bracketright",
    "minus": "minus", "equals": "equal", "tilde": "grave", "capslock": "capslock", "numlock": "numlock",
    "scrolllock": "scrolllock", "pause": "pause", "print": "printscreen",
    "numpadadd": "numadd", "numpadminus": "numsub", "numpadmult": "nummul", "*": "nummul",
    "numpaddivide": "numdiv", "numpaddecimal": "numdec", "numpadenter": "numenter",
    "lshift": "shift", "rshift": "rshift", "lcontrol": "ctrl", "rcontrol": "rctrl",
    "lalt": "alt", "ralt": "ralt", "lwin": "win", "rwin": "win",
    "shift": "shift", "ctrl": "ctrl", "alt": "alt",
}
_BNG_KEYS.update({chr(c): chr(c) for c in range(ord("a"), ord("z") + 1)})
_BNG_KEYS.update({str(i): str(i) for i in range(10)})
_BNG_KEYS.update({f"numpad{i}": f"num{i}" for i in range(10)})
_BNG_KEYS.update({f"f{i}": f"f{i}" for i in range(1, 25)})
_MOD_ORDER = ("ctrl", "rctrl", "shift", "rshift", "alt", "ralt", "win")


def parse_control(control: str) -> list[str] | None:
    """``"ctrl-alt p"`` / ``"shift-ctrl b"`` / ``"numpad5"`` -> FreeDock combo (None if not a known key)."""
    parts = []
    for tok in control.strip().lower().split():
        parts += [tok] if tok == "-" else [p for p in tok.split("-") if p]
    keys = []
    for p in parts:
        k = _BNG_KEYS.get(p, "minus" if p == "-" else None)
        if k is None:
            return None
        keys.append(k)
    if not keys:
        return None
    mods = [m for m in _MOD_ORDER if m in keys[:-1]]
    return mods + [keys[-1]]


def merge_inputmap(base: list[dict], diff: dict | None) -> list[dict]:
    """Apply a BeamNG ``.diff`` to a binding list: drop (control, action) pairs in ``removed``, then
    put ``bindings`` first (so the player's own binding wins when an action has several)."""
    if not diff:
        return list(base)
    removed = {(b.get("control"), b.get("action")) for b in diff.get("removed") or []}
    added = [b for b in diff.get("bindings") or [] if b.get("control") and b.get("action")]
    added_keys = {(b["control"], b["action"]) for b in added}
    rest = [b for b in base if (b.get("control"), b.get("action")) not in removed | added_keys]
    return added + rest


def bindings_from_list(bindings: list[dict]) -> dict[str, Binding]:
    """First keyboard-expressible control per action -> Binding."""
    out: dict[str, Binding] = {}
    for b in bindings:
        action, control = b.get("action"), b.get("control")
        if not action or not isinstance(control, str) or action in out:
            continue
        combo = parse_control(control)
        if combo:
            out[action] = Binding(action, combo)
    return out


def read_bindings(udir: Path | str | None = None, gdir: Path | str | None = None) -> dict[str, Binding]:
    """Keyboard bindings: the game's keyboard.json (or DEFAULT_KEYS) with the user's keyboard.diff on top."""
    udir = Path(udir) if udir else user_dir()
    gdir = Path(gdir) if gdir else game_dir()
    base = _read_json(gdir / "settings" / "inputmaps" / "keyboard.json").get("bindings") if gdir else None
    diff = _read_json(udir / "settings" / "inputmaps" / "keyboard.diff") if udir else None
    if base:
        return bindings_from_list(merge_inputmap(base, diff))
    out = {k: Binding(k, list(v)) for k, v in DEFAULT_KEYS.items()}   # game files not found
    for b in (diff or {}).get("removed") or []:
        out.pop(b.get("action"), None)
    out.update(bindings_from_list((diff or {}).get("bindings") or []))
    return out
