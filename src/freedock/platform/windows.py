"""Windows backend: SendInput (scancodes, so DirectInput games see the keys), CoreAudio via pycaw."""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import time
from ctypes import wintypes

from .keys import WIN_VK, parse_combo

log = logging.getLogger(__name__)
user32 = ctypes.WinDLL("user32", use_last_error=True)

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x4, 0x8
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
MAPVK_VK_TO_VSC = 0
ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)

VK = dict(WIN_VK)

EXTENDED = {"rctrl", "ralt", "insert", "delete", "home", "end", "pageup", "pagedown", "up", "down",
            "left", "right", "numdiv", "numenter", "win", "apps", "printscreen", "numlock"}
# media / browser keys have no useful scancode - send them as virtual keys
VK_ONLY = {"playpause", "nexttrack", "prevtrack", "stop", "mute", "volumedown", "volumeup",
           "browserback", "browserforward", "browserrefresh", "browserhome", "mail", "calculator"}


def vk_of(name: str) -> int:
    if name.startswith("vk:"):
        return int(name[3:], 0)
    if name not in VK:
        raise ValueError(f"unknown key '{name}'")
    return VK[name]


_NAME_OF_VK = {}
for _n, _v in VK.items():
    _NAME_OF_VK.setdefault(_v, _n)


def _key_input(name: str, up: bool) -> INPUT:
    vk = vk_of(name)
    if name.startswith("vk:"):
        name = _NAME_OF_VK.get(vk, name)
    inp = INPUT(type=INPUT_KEYBOARD)
    flags = KEYEVENTF_KEYUP if up else 0
    if name in VK_ONLY:
        inp.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=flags | KEYEVENTF_EXTENDEDKEY, time=0, dwExtraInfo=0)
        return inp
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    flags |= KEYEVENTF_SCANCODE
    if name in EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    inp.ki = KEYBDINPUT(wVk=0, wScan=scan, dwFlags=flags, time=0, dwExtraInfo=0)
    return inp


def _send(inputs: list[INPUT]) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
    if sent != len(inputs):
        log.warning("SendInput sent %d of %d (error %d)", sent, len(inputs), ctypes.get_last_error())


def key_down(name: str) -> None:
    _send([_key_input(name, False)])


def key_up(name: str) -> None:
    _send([_key_input(name, True)])


def press_combo(combo: str | list[str], hold_ms: int = 30) -> None:
    keys = parse_combo(combo) if isinstance(combo, str) else combo
    if not keys:
        return
    _send([_key_input(k, False) for k in keys])
    # games poll input once per frame; a short hold makes sure a frame sees the key down
    time.sleep(hold_ms / 1000)
    _send([_key_input(k, True) for k in reversed(keys)])


def type_text(text: str) -> None:
    inputs = []
    for ch in text:
        code = ord(ch)
        for up in (False, True):
            i = INPUT(type=INPUT_KEYBOARD)
            i.ki = KEYBDINPUT(wVk=0, wScan=code, dwFlags=KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0),
                              time=0, dwExtraInfo=0)
            inputs.append(i)
    if inputs:
        _send(inputs)


def scroll(steps: int, horizontal: bool = False) -> None:
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(dx=0, dy=0, mouseData=ctypes.c_uint32(int(steps) * 120).value,
                      dwFlags=MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL, time=0, dwExtraInfo=0)
    _send([i])


def open_target(target: str) -> None:
    os.startfile(target)  # noqa: S606 - opening user-chosen files/URLs is the point


def launch(path: str, args: str = "", cwd: str | None = None) -> None:
    if not args and not path.lower().endswith((".exe", ".bat", ".cmd")):
        os.startfile(path)
        return
    subprocess.Popen(f'"{path}" {args}'.strip(), cwd=cwd or None, creationflags=0x00000008)  # DETACHED


def run_command(cmd: str) -> None:
    subprocess.Popen(cmd, shell=True, creationflags=0x08000000)  # CREATE_NO_WINDOW


def foreground_process() -> str | None:
    import psutil
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    try:
        return psutil.Process(pid.value).name()
    except (psutil.Error, ValueError):
        return None


# ------------------------------------------------------------------------- audio
PKEY_DEVICE_DESC = "{A45C254E-DF1C-4EFD-8020-67D146A850E0} 2"       # endpoint name ("Headphones")
PKEY_INTERFACE_NAME = "{026E516E-B814-414B-83CD-856D6FEF4822} 2"    # adapter ("Realtek(R) Audio")
OUTPUTS_REFRESH_S = 3.0


class _AudioThread:
    """All CoreAudio (COM) work happens on this one thread.

    COM objects must be released on the apartment that created them; touching them from the
    action worker or the GC on another thread crashes with access violations. Callers only read
    the cached state or post commands to the queue.
    """

    def __init__(self):
        import queue
        import threading
        self.state: dict = {"level": None, "muted": None, "mic_muted": None, "mic_level": None}
        self.outputs: list = []                  # [audio_outputs.Output], refreshed every few seconds
        self.default_output: str | None = None
        self._q: "queue.Queue" = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="coreaudio", daemon=True)
        self._thread.start()

    def post(self, fn_name: str, *args) -> None:
        self._q.put((fn_name, args))

    def _endpoints(self):
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

        def activate(dev):
            if dev is None:
                return None
            raw = getattr(dev, "_dev", dev)   # pycaw >= 2024 wraps the IMMDevice
            # QueryInterface takes its own reference. ctypes.cast() does not, yet both pointers
            # call Release() when collected: the endpoint got released twice and the next call
            # into it crashed the whole process (access violation in _ctypes).
            return raw.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)

        spk = activate(AudioUtilities.GetSpeakers())
        try:
            mic = activate(AudioUtilities.GetMicrophone())
        except Exception:  # noqa: BLE001 - no capture device
            mic = None
        return spk, mic

    @staticmethod
    def _capture_level(name_part: str, db: float, done=None) -> None:
        """Set the recording gain (dB) of a capture endpoint found by name.

        Virtual cables ship at odd levels (Steam's at -17 dB, and its range goes up to +30 dB, so
        "100 %" would be +30 dB and clip): 0 dB passes the audio through unchanged."""
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        found = []
        for dev in AudioUtilities.GetAllDevices():
            name = dev.FriendlyName or ""
            if name_part.lower() not in name.lower() or not (dev.id or "").startswith("{0.0.1."):
                continue
            try:
                raw = getattr(dev, "_dev", dev)
                ep = raw.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None).QueryInterface(IAudioEndpointVolume)
                before = float(ep.GetMasterVolumeLevel())
                lo, hi, _step = ep.GetVolumeRange()
                ep.SetMasterVolumeLevel(max(lo, min(hi, db)), None)
                found.append((name, before))
            except Exception as exc:  # noqa: BLE001 - endpoint not present right now
                log.debug("capture level %s: %s", name, exc)
        if done:
            done(found)

    @staticmethod
    def _list_outputs() -> tuple[list, str | None]:
        """Active playback endpoints and the default one's id (what Ctrl+Win+V lists)."""
        import warnings
        from pycaw.constants import DEVICE_STATE, EDataFlow, ERole
        from pycaw.pycaw import AudioUtilities
        from ..audio_outputs import Output
        with warnings.catch_warnings():          # pycaw warns about unreadable properties
            warnings.simplefilter("ignore")
            devices = AudioUtilities.GetAllDevices(EDataFlow.eRender.value, DEVICE_STATE.ACTIVE.value)
        out = [Output(d.id, d.FriendlyName or d.id, str(d.properties.get(PKEY_DEVICE_DESC) or ""),
                      str(d.properties.get(PKEY_INTERFACE_NAME) or "")) for d in devices if d is not None]
        try:
            default = AudioUtilities.GetDeviceEnumerator().GetDefaultAudioEndpoint(
                EDataFlow.eRender.value, ERole.eMultimedia.value).GetId()
        except Exception:  # noqa: BLE001 - no playback device at all
            default = None
        return out, default

    @staticmethod
    def _set_default_output(dev_id: str, comms: bool) -> None:
        from pycaw.constants import ERole
        from pycaw.pycaw import AudioUtilities
        roles = [ERole.eConsole, ERole.eMultimedia] + ([ERole.eCommunications] if comms else [])
        AudioUtilities.SetDefaultDevice(dev_id, roles)

    def _run(self) -> None:
        import queue
        import comtypes
        comtypes.CoInitialize()
        spk = mic = None
        refreshed = 0.0
        listed = 0.0
        while True:
            try:
                # re-resolve the default devices now and then so a new headset is picked up
                if spk is None or time.monotonic() - refreshed > 15:
                    spk = mic = None
                    spk, mic = self._endpoints()
                    refreshed = time.monotonic()
                if time.monotonic() - listed > OUTPUTS_REFRESH_S:
                    self.outputs, self.default_output = self._list_outputs()
                    listed = time.monotonic()
                try:
                    name, args = self._q.get(timeout=0.4)
                except queue.Empty:
                    name, args = None, ()
                if name == "set_output":
                    dev_id, comms, done = args
                    try:
                        self._set_default_output(dev_id, comms)
                        ok = True
                    except Exception as exc:  # noqa: BLE001 - device vanished, policy refused
                        log.warning("cannot make %s the default output: %s", dev_id, exc)
                        ok = False
                    # volume / mute keys must follow the new device right away, not in 15 s
                    spk = mic = None
                    spk, mic = self._endpoints()
                    refreshed = time.monotonic()
                    self.outputs, self.default_output = self._list_outputs()
                    listed = time.monotonic()
                    if done:
                        done(ok)
                elif name == "list_outputs":
                    self.outputs, self.default_output = self._list_outputs()
                    listed = time.monotonic()
                if name == "change" and spk:
                    spk.SetMasterVolumeLevelScalar(max(0.0, min(1.0, spk.GetMasterVolumeLevelScalar() + args[0])), None)
                elif name == "set" and spk:
                    spk.SetMasterVolumeLevelScalar(max(0.0, min(1.0, args[0])), None)
                elif name == "mute" and spk:
                    spk.SetMute(0 if spk.GetMute() else 1, None)
                elif name == "mic" and mic:
                    mic.SetMute(0 if mic.GetMute() else 1, None)
                elif name == "mic_change" and mic:
                    mic.SetMasterVolumeLevelScalar(max(0.0, min(1.0, mic.GetMasterVolumeLevelScalar() + args[0])), None)
                elif name == "capture_level":
                    self._capture_level(*args)
                self.state = {"level": float(spk.GetMasterVolumeLevelScalar()) if spk else None,
                              "muted": bool(spk.GetMute()) if spk else None,
                              "mic_muted": bool(mic.GetMute()) if mic else None,
                              "mic_level": float(mic.GetMasterVolumeLevelScalar()) if mic else None}
            except Exception as exc:  # noqa: BLE001 - device unplugged, COM hiccup...
                log.debug("coreaudio: %s", exc)
                spk = mic = None
                time.sleep(1)


_audio_thread: _AudioThread | None = None


def _audio() -> _AudioThread:
    global _audio_thread
    if _audio_thread is None:
        _audio_thread = _AudioThread()
    return _audio_thread


def volume() -> tuple[float | None, bool | None]:
    s = _audio().state
    return s["level"], s["muted"]


def change_volume(step: float) -> None:
    _audio().post("change", step)


def set_volume(level: float) -> None:
    _audio().post("set", level)


def toggle_mute() -> None:
    _audio().post("mute")


def mic_state() -> bool | None:
    """True = muted."""
    return _audio().state["mic_muted"]


def toggle_mic() -> None:
    _audio().post("mic")


def mic_level() -> float | None:
    return _audio().state.get("mic_level")


def change_mic_volume(step: float) -> None:
    _audio().post("mic_change", step)


def audio_outputs() -> tuple[list, str | None]:
    """([Output] of the active playback devices, id of the default one). Cached; never blocks."""
    a = _audio()
    return list(a.outputs), a.default_output


def refresh_outputs() -> None:
    _audio().post("list_outputs")


def set_default_output(dev_id: str, comms: bool = False, timeout: float = 3.0) -> bool:
    """Make a playback device the default (console + multimedia roles, like Ctrl+Win+V; comms=True
    also moves calls/Discord's "default communication device"). Blocks until done; worker threads only."""
    import threading
    ev, res = threading.Event(), []
    _audio().post("set_output", dev_id, comms, lambda ok: (res.append(ok), ev.set()))
    ev.wait(timeout)
    return bool(res and res[0])


def set_capture_level(name_part: str, db: float = 0.0, timeout: float = 5.0) -> list[tuple[str, float]]:
    """Recording gain in dB (0 = unchanged) of the capture devices whose name contains ``name_part``.
    Returns [(device name, previous gain in dB)]."""
    import threading
    ev, out = threading.Event(), []
    _audio().post("capture_level", name_part, db, lambda found: (out.extend(found), ev.set()))
    ev.wait(timeout)
    return out
