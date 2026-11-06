"""Linux backend: uinput (evdev) for keys, PipeWire/PulseAudio CLIs for audio. Works on X11 and Wayland."""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time

from .keys import parse_combo

log = logging.getLogger(__name__)

_UI = None
_ecodes = None


def _uinput():
    global _UI, _ecodes
    if _UI is None:
        from evdev import UInput, ecodes
        _ecodes = ecodes
        keys = [v for k, v in ecodes.ecodes.items() if k.startswith(("KEY_", "BTN_LEFT", "BTN_RIGHT"))]
        caps = {ecodes.EV_KEY: sorted(set(keys)), ecodes.EV_REL: [ecodes.REL_WHEEL, ecodes.REL_HWHEEL,
                                                                   ecodes.REL_X, ecodes.REL_Y]}
        _UI = UInput(caps, name="FreeDock virtual keyboard")
        time.sleep(0.2)  # let the compositor pick up the new device
    return _UI, _ecodes


NAMES = {
    "ctrl": "KEY_LEFTCTRL", "shift": "KEY_LEFTSHIFT", "alt": "KEY_LEFTALT", "win": "KEY_LEFTMETA",
    "rctrl": "KEY_RIGHTCTRL", "rshift": "KEY_RIGHTSHIFT", "ralt": "KEY_RIGHTALT",
    "esc": "KEY_ESC", "enter": "KEY_ENTER", "tab": "KEY_TAB", "space": "KEY_SPACE",
    "backspace": "KEY_BACKSPACE", "delete": "KEY_DELETE", "insert": "KEY_INSERT", "home": "KEY_HOME",
    "end": "KEY_END", "pageup": "KEY_PAGEUP", "pagedown": "KEY_PAGEDOWN", "up": "KEY_UP",
    "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT", "printscreen": "KEY_SYSRQ",
    "pause": "KEY_PAUSE", "capslock": "KEY_CAPSLOCK", "numlock": "KEY_NUMLOCK",
    "scrolllock": "KEY_SCROLLLOCK", "apps": "KEY_COMPOSE", "minus": "KEY_MINUS", "equal": "KEY_EQUAL",
    "plus": "KEY_EQUAL", "comma": "KEY_COMMA", "period": "KEY_DOT", "slash": "KEY_SLASH",
    "backslash": "KEY_BACKSLASH", "semicolon": "KEY_SEMICOLON", "quote": "KEY_APOSTROPHE",
    "bracketleft": "KEY_LEFTBRACE", "bracketright": "KEY_RIGHTBRACE", "grave": "KEY_GRAVE",
    "numadd": "KEY_KPPLUS", "numsub": "KEY_KPMINUS", "nummul": "KEY_KPASTERISK",
    "numdiv": "KEY_KPSLASH", "numdec": "KEY_KPDOT", "numenter": "KEY_KPENTER",
    "playpause": "KEY_PLAYPAUSE", "nexttrack": "KEY_NEXTSONG", "prevtrack": "KEY_PREVIOUSSONG",
    "stop": "KEY_STOPCD", "mute": "KEY_MUTE", "volumedown": "KEY_VOLUMEDOWN", "volumeup": "KEY_VOLUMEUP",
    "browserback": "KEY_BACK", "browserforward": "KEY_FORWARD", "browserrefresh": "KEY_REFRESH",
    "browserhome": "KEY_HOMEPAGE", "mail": "KEY_MAIL", "calculator": "KEY_CALC",
}
NAMES.update({chr(c): f"KEY_{chr(c)}" for c in range(ord("0"), ord("9") + 1)})
NAMES.update({chr(c).lower(): f"KEY_{chr(c)}" for c in range(ord("A"), ord("Z") + 1)})
NAMES.update({f"f{i}": f"KEY_F{i}" for i in range(1, 25)})
NAMES.update({f"num{i}": f"KEY_KP{i}" for i in range(10)})


def _code(name: str) -> int:
    ui, ec = _uinput()
    if name not in NAMES:
        raise ValueError(f"unknown key '{name}'")
    return ec.ecodes[NAMES[name]]


def _emit(code: int, value: int) -> None:
    ui, ec = _uinput()
    ui.write(ec.EV_KEY, code, value)
    ui.syn()


def key_down(name: str) -> None:
    _emit(_code(name), 1)


def key_up(name: str) -> None:
    _emit(_code(name), 0)


def press_combo(combo: str | list[str], hold_ms: int = 30) -> None:
    keys = parse_combo(combo) if isinstance(combo, str) else combo
    codes = [_code(k) for k in keys]
    for c in codes:
        _emit(c, 1)
    time.sleep(hold_ms / 1000)
    for c in reversed(codes):
        _emit(c, 0)


def type_text(text: str) -> None:
    for tool, args in (("wtype", [text]), ("ydotool", ["type", text]), ("xdotool", ["type", "--", text])):
        if shutil.which(tool):
            subprocess.run([tool, *args], check=False)
            return
    # last resort: US-layout ASCII through uinput
    shifted = {'"': "quote", ":": "semicolon", "<": "comma", ">": "period", "?": "slash", "!": "1",
               "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
               "_": "minus", "+": "equal", "{": "bracketleft", "}": "bracketright", "|": "backslash", "~": "grave"}
    plain = {" ": "space", "\n": "enter", "\t": "tab", "-": "minus", "=": "equal", ",": "comma", ".": "period",
             "/": "slash", ";": "semicolon", "'": "quote", "[": "bracketleft", "]": "bracketright",
             "\\": "backslash", "`": "grave"}
    for ch in text:
        if ch.isalpha() and ch.isascii():
            press_combo(["shift", ch.lower()] if ch.isupper() else [ch], 5)
        elif ch.isdigit():
            press_combo([ch], 5)
        elif ch in plain:
            press_combo([plain[ch]], 5)
        elif ch in shifted:
            press_combo(["shift", shifted[ch]], 5)


def scroll(steps: int, horizontal: bool = False) -> None:
    ui, ec = _uinput()
    ui.write(ec.EV_REL, ec.REL_HWHEEL if horizontal else ec.REL_WHEEL, int(steps))
    ui.syn()


def open_target(target: str) -> None:
    subprocess.Popen(["xdg-open", target], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def launch(path: str, args: str = "", cwd: str | None = None) -> None:
    import shlex
    subprocess.Popen([path, *shlex.split(args)], cwd=cwd or None, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_command(cmd: str) -> None:
    subprocess.Popen(cmd, shell=True, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def foreground_process() -> str | None:
    """Best effort: works on X11 (xdotool), KDE Wayland (kdotool) and Hyprland."""
    import psutil
    for cmd in (["kdotool", "getactivewindow", "getwindowpid"], ["xdotool", "getactivewindow", "getwindowpid"]):
        if shutil.which(cmd[0]):
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=1).stdout.strip()
            if out.isdigit():
                try:
                    return psutil.Process(int(out)).name()
                except psutil.Error:
                    return None
    if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
        import json
        out = subprocess.run(["hyprctl", "activewindow", "-j"], capture_output=True, text=True, timeout=1).stdout
        try:
            return psutil.Process(json.loads(out)["pid"]).name()
        except (ValueError, KeyError, psutil.Error):
            return None
    return None


# ------------------------------------------------------------------------- audio
def _wpctl(*args: str) -> str:
    return subprocess.run(["wpctl", *args], capture_output=True, text=True, timeout=2).stdout


def _get(target: str) -> tuple[float | None, bool | None]:
    if shutil.which("wpctl"):
        out = _wpctl("get-volume", target)
        m = re.search(r"Volume:\s*([\d.]+)", out)
        if m:
            return float(m.group(1)), "MUTED" in out
    if shutil.which("pactl"):
        kind = "sink" if "SINK" in target else "source"
        dflt = f"@DEFAULT_{kind.upper()}@"
        vol = subprocess.run(["pactl", f"get-{kind}-volume", dflt], capture_output=True, text=True).stdout
        mute = subprocess.run(["pactl", f"get-{kind}-mute", dflt], capture_output=True, text=True).stdout
        m = re.search(r"(\d+)%", vol)
        if m:
            return int(m.group(1)) / 100, "yes" in mute
    return None, None


SINK, SOURCE = "@DEFAULT_AUDIO_SINK@", "@DEFAULT_AUDIO_SOURCE@"


def volume() -> tuple[float | None, bool | None]:
    return _get(SINK)


def change_volume(step: float) -> None:
    pct = f"{abs(step) * 100:.0f}%{'+' if step > 0 else '-'}"
    if shutil.which("wpctl"):
        _wpctl("set-volume", "-l", "1.0", SINK, pct)
    elif shutil.which("pactl"):
        subprocess.run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{'+' if step > 0 else '-'}{abs(step) * 100:.0f}%"])
    else:
        press_combo("volumeup" if step > 0 else "volumedown")


def set_volume(level: float) -> None:
    if shutil.which("wpctl"):
        _wpctl("set-volume", SINK, f"{max(0.0, min(1.0, level)):.2f}")


def toggle_mute() -> None:
    if shutil.which("wpctl"):
        _wpctl("set-mute", SINK, "toggle")
    elif shutil.which("pactl"):
        subprocess.run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", "toggle"])
    else:
        press_combo("mute")


def mic_state() -> bool | None:
    return _get(SOURCE)[1]


def toggle_mic() -> None:
    if shutil.which("wpctl"):
        _wpctl("set-mute", SOURCE, "toggle")
    elif shutil.which("pactl"):
        subprocess.run(["pactl", "set-source-mute", "@DEFAULT_SOURCE@", "toggle"])


def mic_level() -> float | None:
    return _get(SOURCE)[0]


def change_mic_volume(step: float) -> None:
    pct = f"{abs(step) * 100:.0f}%{'+' if step > 0 else '-'}"
    if shutil.which("wpctl"):
        _wpctl("set-volume", "-l", "1.0", SOURCE, pct)
    elif shutil.which("pactl"):
        subprocess.run(["pactl", "set-source-volume", "@DEFAULT_SOURCE@", f"{'+' if step > 0 else '-'}{abs(step) * 100:.0f}%"])


def set_capture_level(name_part: str, db: float = 0.0, timeout: float = 5.0) -> list:
    return []
