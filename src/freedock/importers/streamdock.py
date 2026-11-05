"""Import N4 profiles from the official StreamDock app (HotSpot / MiraBox).

Profiles live in ``%APPDATA%\\HotSpot\\StreamDock\\profiles\\*.sdProfile\\manifest.json``. Actions
are keyed "column,row": rows 0-1 are the LCD keys, row 2 the strip. Icons stored as PNG are copied;
``.mbg`` icons use a proprietary obfuscated format and are skipped (FreeDock picks an icon instead).
"""
from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from ..platform.keys import WIN_VK
from ..profile import Knob, Page, Slot, config_dir

log = logging.getLogger(__name__)

_NAME_OF_VK = {}
for _name, _vk in WIN_VK.items():
    _NAME_OF_VK.setdefault(_vk, _name)

MULTIMEDIA = {0: ("playpause", "play"), 1: ("prevtrack", "skip-back"), 2: ("nexttrack", "skip-forward"),
              3: ("stop", "square")}


def default_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home())) / "HotSpot" / "StreamDock" / "profiles"


def _combo(h: dict) -> str | None:
    vk = h.get("VKeyCode", -1)
    if vk is None or vk < 0:
        return None
    mods = [m for m, f in (("win", "KeyCmd"), ("ctrl", "KeyCtrl"), ("alt", "KeyOption"), ("shift", "KeyShift"))
            if h.get(f)]
    return "+".join(mods + [_NAME_OF_VK.get(vk, f"vk:{vk:#04x}")])


def _convert(a: dict) -> tuple[dict | None, dict | None, str | None]:
    """-> (action, widget, icon) for one official action."""
    uuid = a.get("UUID", "")
    s = a.get("Settings") or {}
    kind = uuid.rsplit(".", 1)[-1] if uuid.count(".") > 2 else uuid
    if uuid.endswith("system.hotkey"):
        combos = [c for c in (_combo(h) for h in s.get("Hotkeys", [])) if c]
        if not combos:
            return None, None, "keyboard"
        if len(combos) == 1:
            return {"type": "hotkey", "keys": combos[0]}, None, "keyboard"
        return {"type": "multi", "steps": [{"type": "hotkey", "keys": c} for c in combos], "delay_ms": 80}, None, "keyboard"
    if uuid.endswith("system.website"):
        return {"type": "open", "target": s.get("path", "")}, None, "globe"
    if uuid.endswith("system.open"):
        return {"type": "open", "target": s.get("path", "")}, None, "folder-open"
    if uuid.endswith("system.multimedia"):
        op, icon = MULTIMEDIA.get(int(s.get("actionIdx", 0)) % 4, ("playpause", "play"))
        return {"type": "media", "op": op}, None, icon
    if uuid.endswith("system.volume"):
        return None, {"type": "volume"}, "volume-2"
    if "microphone" in uuid:
        return None, {"type": "mic"}, "mic"
    if uuid.endswith("device.brightness"):
        return {"type": "brightness", "op": "up", "step": 10}, None, "sun"
    if uuid.endswith("page.change") or uuid.endswith("profile.rotate"):
        return {"type": "page", "target": "next"}, None, "layers"
    if uuid.endswith("page.indicator"):
        return None, {"type": "page_name"}, "layers"
    if "time.action1" in uuid:
        return None, {"type": "clock", "format": "24h", "date": True}, "clock"
    if "time.action2" in uuid:
        return None, {"type": "stopwatch"}, "timer-reset"
    if "time.action3" in uuid:
        return None, {"type": "pomodoro", "minutes": max(1, int(s.get("timing", 1500000)) // 60000)}, "timer"
    if "calendar" in uuid:
        return None, {"type": "date"}, "calendar"
    if "weather" in uuid:
        return None, {"type": "weather"}, "cloud-sun"
    if "obsstudio" in uuid:
        op = {"record": "record", "stream": "stream", "scene": "scene", "mixeraudio": "mute"}.get(kind)
        if op:
            return {"type": "obs", "op": op, "name": s.get("scene", "")}, None, "video"
    log.info("StreamDock action %s has no FreeDock equivalent yet", uuid)
    return None, None, None


def _copy_image(profile_dir: Path, rel: str, dest_dir: Path) -> str | None:
    if not rel or rel.lower().endswith(".mbg"):
        return None
    src = profile_dir / rel
    if not src.is_file():
        src = profile_dir / "Images" / Path(rel).name
    if not src.is_file() or src.suffix.lower() not in (".png", ".jpg", ".jpeg", ".gif", ".bmp"):
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{profile_dir.stem[:8]}-{src.name}"
    shutil.copyfile(src, dest)
    return str(dest)


def _page_from(manifest: Path, name_hint: str) -> Page | None:
    d = json.loads(manifest.read_text(encoding="utf-8"))
    if "N4" not in (d.get("DeviceUUID") or ""):
        return None
    page = Page.new(d.get("Name") or name_hint)
    page.accent = "#f59e0b"
    images = config_dir() / "images"
    for pos, a in (d.get("Actions") or {}).items():
        try:
            col, row = (int(x) for x in pos.split(","))
        except ValueError:
            continue
        action, widget, icon = _convert(a)
        if action is None and widget is None:
            continue
        states = a.get("States") or [{}]
        image = _copy_image(manifest.parent, states[0].get("Image", ""), images)
        slot = Slot(label=(a.get("Name") or "")[:14] if not widget else "", icon=None if image else icon,
                    image=image, action=action, widget=widget, style={"bg": "#1e2533", "bg2": "#0d1118"})
        if row in (0, 1) and 0 <= col < 5:
            page.keys[row * 5 + col] = slot
        elif row == 2 and 0 <= col < 4:
            page.zones[col] = slot
            if action:
                page.knobs[col] = Knob(press=action)
    return page


def import_profiles(base: Path | None = None) -> list[Page]:
    base = base or default_dir()
    pages = []
    for manifest in sorted(base.glob("*.sdProfile/manifest.json")):
        try:
            page = _page_from(manifest, manifest.parent.stem[:8])
        except (OSError, ValueError) as exc:
            log.warning("skip %s: %s", manifest, exc)
            continue
        if page and any(not s.empty for s in page.keys + page.zones):
            page.name = f"StreamDock · {page.name}"
            pages.append(page)
    return pages
