"""Microsoft Flight Simulator 2020/2024 through SimConnect.dll (ctypes, no SDK needed).

One FLOAT64 datum per data definition, delivered on change. Anything on the deck can subscribe
to a SimVar by name ("INDICATED ALTITUDE", "feet") and fire any K: event ("AP_MASTER").

SimConnect.dll is not shipped with the sim; FreeDock looks for it in the MSFS SDK folders, next to
the app, in settings, and in the Logitech MSFS plugin folder (which bundles a compatible x64 copy).
"""
from __future__ import annotations

import ctypes
import logging
import os
import struct
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

USER_OBJECT = 0
UNUSED = 0xFFFFFFFF
GROUP_PRIORITY_HIGHEST = 1
EVENT_FLAG_GROUPID_IS_PRIORITY = 0x10
DATATYPE_FLOAT64 = 4
PERIOD_SIM_FRAME = 3
FLAG_CHANGED = 1
RECV_EXCEPTION, RECV_OPEN, RECV_QUIT, RECV_SIMOBJECT_DATA = 1, 2, 3, 8

# SimVars every FreeDock MSFS page wants; others are added on demand.
CORE_VARS = [
    ("INDICATED ALTITUDE", "feet"), ("AIRSPEED INDICATED", "knots"), ("VERTICAL SPEED", "feet per minute"),
    ("PLANE HEADING DEGREES MAGNETIC", "degrees"), ("AUTOPILOT MASTER", "bool"),
    ("AUTOPILOT HEADING LOCK", "bool"), ("AUTOPILOT HEADING LOCK DIR", "degrees"),
    ("AUTOPILOT ALTITUDE LOCK", "bool"), ("AUTOPILOT ALTITUDE LOCK VAR", "feet"),
    ("AUTOPILOT VERTICAL HOLD", "bool"), ("AUTOPILOT VERTICAL HOLD VAR", "feet per minute"),
    ("AUTOPILOT NAV1 LOCK", "bool"), ("AUTOPILOT APPROACH HOLD", "bool"), ("AUTOPILOT FLIGHT LEVEL CHANGE", "bool"),
    ("AUTOPILOT YAW DAMPER", "bool"), ("KOHLSMAN SETTING HG", "inHg"), ("GEAR HANDLE POSITION", "bool"),
    ("FLAPS HANDLE INDEX", "number"), ("BRAKE PARKING POSITION", "bool"), ("LIGHT LANDING", "bool"),
    ("LIGHT STROBE", "bool"), ("LIGHT NAV", "bool"), ("LIGHT BEACON", "bool"), ("LIGHT TAXI", "bool"),
    ("SIM ON GROUND", "bool"), ("COM ACTIVE FREQUENCY:1", "MHz"), ("SPOILERS HANDLE POSITION", "percent"),
    ("GENERAL ENG THROTTLE LEVER POSITION:1", "percent"),
]


def _candidates(override: str) -> list[Path]:
    paths = []
    if override:
        paths.append(Path(override))
    here = Path(sys.argv[0]).resolve().parent
    paths.append(here / "SimConnect.dll")
    for var in ("MSFS2024_SDK", "MSFS_SDK"):
        if sdk := os.environ.get(var):
            paths.append(Path(sdk) / "SimConnect SDK" / "lib" / "SimConnect.dll")
    paths += [Path(r"C:\MSFS 2024 SDK\SimConnect SDK\lib\SimConnect.dll"),
              Path(r"C:\MSFS SDK\SimConnect SDK\lib\SimConnect.dll"),
              Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Logitech"
              / "Microsoft Flight Simulator Plugin" / "SimConnect.dll"]
    try:
        import SimConnect as _pkg  # the "SimConnect" pip package bundles the DLL
        paths.append(Path(_pkg.__file__).parent / "SimConnect.dll")
    except ImportError:
        pass
    return paths


def find_dll(override: str = "") -> Path | None:
    return next((p for p in _candidates(override) if p.is_file()), None)


class SimConnectClient:
    """Background connection to the sim. ``values`` maps 'NAME' -> float, updated on change."""

    def __init__(self):
        self.values: dict[str, float] = {}
        self.connected = False
        self.dll_path: Path | None = None
        self.override = ""
        self._dll = None
        self._handle = ctypes.c_void_p()
        self._vars: dict[str, tuple[int, str]] = {}      # name -> (define id, units)
        self._by_id: dict[int, str] = {}
        self._wanted: list[tuple[str, str]] = list(CORE_VARS)
        self._events: dict[str, int] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    # ----------------------------------------------------------------- public
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        if os.environ.get("FREEDOCK_DEMO"):
            self._stop.clear()
            self._thread = threading.Thread(target=self._demo, name="simconnect-demo", daemon=True)
            self._thread.start()
            return
        if sys.platform != "win32":
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="simconnect", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def watch(self, name: str, units: str = "number") -> None:
        name = name.upper()
        with self._lock:
            if any(n == name for n, _ in self._wanted):
                return
            self._wanted.append((name, units))
            if self.connected:
                self._subscribe(name, units)

    def get(self, name: str, default: float | None = None) -> float | None:
        return self.values.get(name.upper(), default)

    def _demo(self) -> None:
        """A Cessna in cruise with the autopilot on (FREEDOCK_DEMO=1)."""
        import math
        import time
        self.connected = True
        t0 = time.time()
        self.values.update({"AUTOPILOT MASTER": 1, "AUTOPILOT HEADING LOCK": 1, "AUTOPILOT ALTITUDE LOCK": 1,
                            "AUTOPILOT HEADING LOCK DIR": 270, "AUTOPILOT ALTITUDE LOCK VAR": 5500,
                            "AUTOPILOT VERTICAL HOLD VAR": 0, "KOHLSMAN SETTING HG": 29.92, "GEAR HANDLE POSITION": 1,
                            "FLAPS HANDLE INDEX": 0, "LIGHT BEACON": 1, "LIGHT NAV": 1, "LIGHT STROBE": 1,
                            "COM ACTIVE FREQUENCY:1": 122.800, "SPOILERS HANDLE POSITION": 0})
        while not self._stop.is_set():
            t = time.time() - t0
            self.values.update({"INDICATED ALTITUDE": 5500 + 12 * math.sin(t / 3),
                                "AIRSPEED INDICATED": 118 + 2 * math.sin(t / 5),
                                "VERTICAL SPEED": 40 * math.sin(t / 3),
                                "PLANE HEADING DEGREES MAGNETIC": 270 + 1.5 * math.sin(t / 7)})
            self._stop.wait(1 / 20)

    def _demo_event(self, event: str) -> None:
        v = self.values
        toggles = {"AP_MASTER": "AUTOPILOT MASTER", "AP_NAV1_HOLD": "AUTOPILOT NAV1 LOCK",
                   "AP_APR_HOLD": "AUTOPILOT APPROACH HOLD", "FLIGHT_LEVEL_CHANGE": "AUTOPILOT FLIGHT LEVEL CHANGE",
                   "YAW_DAMPER_TOGGLE": "AUTOPILOT YAW DAMPER", "GEAR_TOGGLE": "GEAR HANDLE POSITION",
                   "PARKING_BRAKES": "BRAKE PARKING POSITION", "LANDING_LIGHTS_TOGGLE": "LIGHT LANDING",
                   "STROBES_TOGGLE": "LIGHT STROBE", "TOGGLE_BEACON_LIGHTS": "LIGHT BEACON",
                   "TOGGLE_NAV_LIGHTS": "LIGHT NAV", "TOGGLE_TAXI_LIGHTS": "LIGHT TAXI",
                   "AP_PANEL_HEADING_HOLD": "AUTOPILOT HEADING LOCK", "AP_PANEL_ALTITUDE_HOLD": "AUTOPILOT ALTITUDE LOCK",
                   "AP_PANEL_VS_HOLD": "AUTOPILOT VERTICAL HOLD"}
        steps = {"HEADING_BUG_INC": ("AUTOPILOT HEADING LOCK DIR", 1),
                 "HEADING_BUG_DEC": ("AUTOPILOT HEADING LOCK DIR", -1),
                 "AP_ALT_VAR_INC": ("AUTOPILOT ALTITUDE LOCK VAR", 100),
                 "AP_ALT_VAR_DEC": ("AUTOPILOT ALTITUDE LOCK VAR", -100),
                 "AP_VS_VAR_INC": ("AUTOPILOT VERTICAL HOLD VAR", 100),
                 "AP_VS_VAR_DEC": ("AUTOPILOT VERTICAL HOLD VAR", -100),
                 "KOHLSMAN_INC": ("KOHLSMAN SETTING HG", 0.01), "KOHLSMAN_DEC": ("KOHLSMAN SETTING HG", -0.01),
                 "FLAPS_INCR": ("FLAPS HANDLE INDEX", 1), "FLAPS_DECR": ("FLAPS HANDLE INDEX", -1)}
        if event in toggles:
            v[toggles[event]] = 0.0 if v.get(toggles[event]) else 1.0
        elif event in steps:
            name, d = steps[event]
            val = v.get(name, 0) + d
            if name == "AUTOPILOT HEADING LOCK DIR":
                val %= 360
            if name == "FLAPS HANDLE INDEX":
                val = max(0, min(4, val))
            v[name] = val

    def send_event(self, event: str, data: int = 0) -> bool:
        if os.environ.get("FREEDOCK_DEMO"):
            self._demo_event(event.upper())
            return True
        if not self.connected:
            return False
        event = event.upper()
        with self._lock:
            if event not in self._events:
                eid = 1000 + len(self._events)
                if self._dll.SimConnect_MapClientEventToSimEvent(self._handle, eid, event.encode()) < 0:
                    return False
                self._events[event] = eid
            return self._dll.SimConnect_TransmitClientEvent(
                self._handle, USER_OBJECT, self._events[event], ctypes.c_uint32(data & 0xFFFFFFFF).value,
                GROUP_PRIORITY_HIGHEST, EVENT_FLAG_GROUPID_IS_PRIORITY) >= 0

    # ----------------------------------------------------------------- internals
    def _load(self) -> bool:
        if self._dll is not None:
            return True
        path = find_dll(self.override)
        if path is None:
            return False
        dll = ctypes.WinDLL(str(path))
        vp, u32, cp = ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p
        dll.SimConnect_Open.argtypes = (ctypes.POINTER(vp), cp, vp, u32, vp, u32)
        dll.SimConnect_Close.argtypes = (vp,)
        dll.SimConnect_AddToDataDefinition.argtypes = (vp, u32, cp, cp, u32, ctypes.c_float, u32)
        dll.SimConnect_RequestDataOnSimObject.argtypes = (vp, u32, u32, u32, u32, u32, u32, u32, u32)
        dll.SimConnect_MapClientEventToSimEvent.argtypes = (vp, u32, cp)
        dll.SimConnect_TransmitClientEvent.argtypes = (vp, u32, u32, u32, u32, u32)
        dll.SimConnect_GetNextDispatch.argtypes = (vp, ctypes.POINTER(vp), ctypes.POINTER(u32))
        for fn in ("Open", "Close", "AddToDataDefinition", "RequestDataOnSimObject",
                   "MapClientEventToSimEvent", "TransmitClientEvent", "GetNextDispatch"):
            getattr(dll, f"SimConnect_{fn}").restype = ctypes.c_long
        self._dll, self.dll_path = dll, path
        log.info("SimConnect: %s", path)
        return True

    def _subscribe(self, name: str, units: str) -> None:
        did = len(self._vars) + 1
        self._vars[name] = (did, units)
        self._by_id[did] = name
        self._dll.SimConnect_AddToDataDefinition(self._handle, did, name.encode(), units.encode(),
                                                 DATATYPE_FLOAT64, 0.0, UNUSED)
        self._dll.SimConnect_RequestDataOnSimObject(self._handle, did, did, USER_OBJECT, PERIOD_SIM_FRAME,
                                                    FLAG_CHANGED, 0, 0, 0)

    def _open(self) -> bool:
        h = ctypes.c_void_p()
        if self._dll.SimConnect_Open(ctypes.byref(h), b"FreeDock", None, 0, None, 0) < 0:
            return False
        self._handle = h
        with self._lock:
            self._vars.clear()
            self._by_id.clear()
            self._events.clear()
            for name, units in self._wanted:
                self._subscribe(name, units)
        return True

    def _close(self) -> None:
        if self._handle:
            self._dll.SimConnect_Close(self._handle)
        self._handle = ctypes.c_void_p()
        self.connected = False
        self.values = {}

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self._load():
                self._stop.wait(10)
                continue
            if not self.connected:
                if self._open():
                    self.connected = True
                    log.info("SimConnect connected")
                else:
                    self._stop.wait(3)
                    continue
            self._drain()
            self._stop.wait(1 / 30)
        self._close()

    def _drain(self) -> None:
        data, size = ctypes.c_void_p(), ctypes.c_uint32()
        while self._handle and self._dll.SimConnect_GetNextDispatch(self._handle, ctypes.byref(data),
                                                                    ctypes.byref(size)) >= 0:
            if not data.value or size.value < 12:
                break
            raw = ctypes.string_at(data.value, size.value)
            rid = struct.unpack_from("<I", raw, 8)[0]
            if rid == RECV_SIMOBJECT_DATA and size.value >= 48:
                req = struct.unpack_from("<I", raw, 12)[0]
                name = self._by_id.get(req)
                if name:
                    self.values[name] = struct.unpack_from("<d", raw, 40)[0]
            elif rid == RECV_QUIT:
                log.info("SimConnect: sim closed")
                self._close()
                return
            elif rid == RECV_EXCEPTION:
                code, index = struct.unpack_from("<I", raw, 12)[0], struct.unpack_from("<I", raw, 20)[0]
                log.debug("SimConnect exception %d (index %d)", code, index)


sim = SimConnectClient()
