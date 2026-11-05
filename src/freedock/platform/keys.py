"""Portable key names. Hotkeys are written as ``"ctrl+shift+esc"``; names are case-insensitive."""
from __future__ import annotations

ALIASES = {
    "control": "ctrl", "lctrl": "ctrl", "option": "alt", "lalt": "alt", "menu": "alt",
    "super": "win", "meta": "win", "cmd": "win", "windows": "win", "lwin": "win",
    "escape": "esc", "return": "enter", "del": "delete", "ins": "insert",
    "pgup": "pageup", "pgdn": "pagedown", "prtsc": "printscreen", "print": "printscreen",
    "dot": "period", ".": "period", ",": "comma", "-": "minus", "=": "equal", "+": "plus",
    "/": "slash", "\\": "backslash", ";": "semicolon", "'": "quote", "[": "bracketleft",
    "]": "bracketright", "`": "grave", "backtick": "grave", "arrowup": "up", "arrowdown": "down",
    "arrowleft": "left", "arrowright": "right", "play": "playpause", "next": "nexttrack",
    "prev": "prevtrack", "previous": "prevtrack", "volup": "volumeup", "voldown": "volumedown",
    "volumemute": "mute", "spacebar": "space", "bksp": "backspace", "caps": "capslock",
    "numplus": "numadd", "numminus": "numsub", "numstar": "nummul", "numslash": "numdiv",
}

MODIFIERS = ("ctrl", "shift", "alt", "win", "rctrl", "rshift", "ralt")


def normalize(name: str) -> str:
    n = name.strip().lower()
    if n == "+":
        return "plus"
    return ALIASES.get(n, n)


def parse_combo(combo: str) -> list[str]:
    """``"Ctrl+Shift+Esc"`` -> ``["ctrl", "shift", "esc"]``. A trailing ``++`` means the plus key."""
    combo = combo.strip()
    if not combo:
        return []
    parts, buf = [], ""
    i = 0
    while i < len(combo):
        ch = combo[i]
        if ch == "+" and buf:
            parts.append(buf)
            buf = ""
        elif ch == "+" and not buf:
            parts.append("plus")
            if i + 1 < len(combo) and combo[i + 1] == "+":
                i += 1
        else:
            buf += ch
        i += 1
    if buf:
        parts.append(buf)
    return [normalize(p) for p in parts if p.strip()]


def pretty(combo: str) -> str:
    names = {"win": "Win", "ctrl": "Ctrl", "shift": "Shift", "alt": "Alt", "esc": "Esc"}
    return "+".join(names.get(k, k.upper() if len(k) == 1 else k.capitalize()) for k in parse_combo(combo))


# Windows virtual-key codes by FreeDock key name (also used to read StreamDock / AC configs on any OS)
WIN_VK = {
    "ctrl": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "rctrl": 0xA3, "rshift": 0xA1, "ralt": 0xA5,
    "esc": 0x1B, "enter": 0x0D, "tab": 0x09, "space": 0x20, "backspace": 0x08, "delete": 0x2E,
    "insert": 0x2D, "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "printscreen": 0x2C, "pause": 0x13,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91, "apps": 0x5D,
    "minus": 0xBD, "equal": 0xBB, "plus": 0xBB, "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
    "backslash": 0xDC, "semicolon": 0xBA, "quote": 0xDE, "bracketleft": 0xDB, "bracketright": 0xDD,
    "grave": 0xC0, "numadd": 0x6B, "numsub": 0x6D, "nummul": 0x6A, "numdiv": 0x6F, "numdec": 0x6E,
    "numenter": 0x0D,
    "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1, "stop": 0xB2, "mute": 0xAD,
    "volumedown": 0xAE, "volumeup": 0xAF, "browserback": 0xA6, "browserforward": 0xA7,
    "browserrefresh": 0xA8, "browserhome": 0xAC, "mail": 0xB4, "calculator": 0xB7,
}
WIN_VK.update({chr(c): c for c in range(ord("0"), ord("9") + 1)})
WIN_VK.update({chr(c).lower(): c for c in range(ord("A"), ord("Z") + 1)})
WIN_VK.update({f"f{i}": 0x6F + i for i in range(1, 25)})
WIN_VK.update({f"num{i}": 0x60 + i for i in range(10)})

