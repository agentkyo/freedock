"""Audio output switching, the deck's version of Windows' Ctrl+Win+V panel.

The OS backends list the active playback devices and change the default one; this module holds the
pure parts: how a device is named on a small key, which devices are skipped (virtual cables) and
which one comes next.
"""
from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Output:
    id: str                 # endpoint id (Windows) / sink name (Linux)
    name: str               # full friendly name: "JBL LED BRANCO (Realtek(R) Audio)"
    desc: str = ""          # endpoint name, renameable in Sound settings: "JBL LED BRANCO", "Headphones"
    iface: str = ""         # the adapter: "Realtek(R) Audio", "5- Astro A50 Game"


# Never cycled into by default: virtual cables (the soundboard's microphone, VR, streaming) - picking
# one silences the PC. Users can change the list per key.
DEFAULT_SKIP = "CABLE, VB-Audio, Voicemeeter, Steam Streaming, Oculus Virtual, Virtual Audio, Sonar"

# endpoint names Windows (and drivers) give by default: they say nothing, the adapter name does
GENERIC = {
    "speakers", "speaker", "headphones", "headset", "headset earphone", "earphone", "line", "line out",
    "digital output", "digital audio (s/pdif)", "spdif", "realtek digital output", "output", "analog output",
    "alto-falantes", "alto-falante", "fones de ouvido", "fone de ouvido", "saída digital", "altavoces",
    "auriculares", "salida digital", "lautsprecher", "kopfhörer", "haut-parleurs", "casque",
}

# adapters that say nothing either (every HDMI monitor is "NVIDIA High Definition Audio")
_GENERIC_IFACE = re.compile(r"high definition audio|realtek|usb audio( device)?$|^usb|intel|amd|nvidia|"
                            r"display audio|smart sound", re.I)


def split_name(friendly: str) -> tuple[str, str]:
    """'Speakers (Realtek(R) Audio)' -> ('Speakers', 'Realtek(R) Audio'); nested parentheses are kept."""
    friendly = (friendly or "").strip()
    if not friendly.endswith(")"):
        return friendly, ""
    depth = 0
    for i in range(len(friendly) - 1, -1, -1):
        c = friendly[i]
        if c == ")":
            depth += 1
        elif c == "(":
            depth -= 1
            if depth == 0:
                return friendly[:i].strip() or friendly, friendly[i + 1:-1].strip()
    return friendly, ""


def clean_iface(iface: str) -> str:
    """'5- Astro A50 Game' -> 'Astro A50 Game', 'Realtek(R) Audio' -> 'Realtek Audio'."""
    s = re.sub(r"^\d+\s*-\s*", "", iface or "")
    s = re.sub(r"\((R|TM|C)\)|[®™]", "", s, flags=re.I)
    s = s.replace("High Definition Audio", "HD Audio")
    return re.sub(r"\s{2,}", " ", s).strip()


def _parts(o: Output) -> tuple[str, str]:
    desc, iface = o.desc, o.iface
    if not desc and not iface:
        desc, iface = split_name(o.name)
    return desc.strip(), clean_iface(iface)


def title(o: Output) -> tuple[str, str]:
    """(big text, small text) for a key: the name that tells devices apart goes big.

    A renamed endpoint ("JBL LED BRANCO", "MONITOR ESQUERDA") is the user's own name: it wins. A
    generic one ("Headphones") gives way to the adapter ("Astro A50 Game")."""
    desc, iface = _parts(o)
    if not desc:
        return iface or o.name, ""
    if desc.lower() in GENERIC and iface and not _GENERIC_IFACE.search(iface):
        return iface, desc
    return desc, iface


_HEADSET = re.compile(r"headset|headphone|earphone|fone|auricular|casque|kopfh|airpods|buds|a50|arctis|"
                      r"blackshark|kraken|hyperx|cloud", re.I)
_SCREEN = re.compile(r"\b(tv|hdmi|dp|displayport|display|monitor|tela|lg|samsung)\b|nvidia", re.I)


def icon_for(o: Output) -> str:
    text = f"{o.name} {o.desc} {o.iface}"
    if _HEADSET.search(text):
        return "headphones"
    if _SCREEN.search(text):
        return "monitor-speaker"
    return "speaker"


def skip_list(text: str | None) -> list[str]:
    if text is None:
        text = DEFAULT_SKIP
    return [s.strip().lower() for s in str(text).split(",") if s.strip()]


def usable(outputs: Sequence[Output], skip: str | None = None, keep: str | None = None) -> list[Output]:
    """Devices to cycle through, sorted by name. `keep` (the current default) stays in the list even if
    skipped, so the key can show where it is and step away from it."""
    words = skip_list(skip)
    out = [o for o in outputs if o.id == keep or not any(w in o.name.lower() for w in words)]
    return sorted(out, key=lambda o: (title(o)[0].lower(), o.name.lower()))


def step(outputs: Sequence[Output], current: str | None, direction: int = 1,
         skip: str | None = None) -> Output | None:
    """The device after (direction=1) or before (-1) the current default; None when there is no other."""
    ring = usable(outputs, skip, keep=current)
    idx = next((i for i, o in enumerate(ring) if o.id == current), None)
    if idx is None:
        return ring[0 if direction >= 0 else -1] if ring else None
    if len(ring) < 2:
        return None
    return ring[(idx + (1 if direction >= 0 else -1)) % len(ring)]


def find(outputs: Sequence[Output], name: str) -> Output | None:
    """A device by (part of) its name, exact matches first."""
    want = (name or "").strip().lower()
    if not want:
        return None
    for o in outputs:
        if want in (o.name.lower(), o.desc.lower(), o.id.lower()):
            return o
    return next((o for o in outputs if want in o.name.lower()), None)


def position(outputs: Sequence[Output], current: str | None, skip: str | None = None) -> tuple[int, int]:
    """(1-based index of the default, count) among the cycled devices; (0, n) when it is not one of them."""
    ring = usable(outputs, skip, keep=current)
    idx = next((i for i, o in enumerate(ring) if o.id == current), -1)
    return idx + 1, len(ring)
