"""Profile model: pages of keys, strip zones and knobs, saved as readable JSON.

A *slot* is anything with a picture (key or strip zone). It shows a label/icon or a live widget,
and runs an action when pressed. A *knob* has three actions: left, right and push. Every page
also has a swipe binding for the strip.
"""
from __future__ import annotations

import json
import os
import sys
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA = 1
KEYS, ZONES, KNOBS = 10, 4, 4

Action = dict[str, Any]


def config_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "FreeDock"
    base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "freedock"


@dataclass
class Slot:
    label: str = ""
    icon: str | None = None
    style: dict[str, Any] = field(default_factory=dict)   # see style.py: colours, font, animation...
    image: str | None = None
    action: Action | None = None
    long_action: Action | None = None
    widget: dict[str, Any] | None = None

    @property
    def empty(self) -> bool:
        return not (self.label or self.icon or self.image or self.action or self.widget)

    @classmethod
    def from_dict(cls, d: dict | None) -> "Slot":
        d = d or {}
        return cls(label=d.get("label", ""), icon=d.get("icon"), style=dict(d.get("style") or {}),
                   image=d.get("image"), action=d.get("action"), long_action=d.get("long_action"),
                   widget=d.get("widget"))

    def to_dict(self) -> dict | None:
        if self.empty:
            return None
        return {k: v for k, v in asdict(self).items() if v not in (None, "", {}, [])}


@dataclass
class Knob:
    left: Action | None = None
    right: Action | None = None
    press: Action | None = None

    @classmethod
    def from_dict(cls, d: dict | None) -> "Knob":
        d = d or {}
        return cls(left=d.get("left"), right=d.get("right"), press=d.get("press"))

    def to_dict(self) -> dict | None:
        out = {k: v for k, v in asdict(self).items() if v}
        return out or None


@dataclass
class Page:
    id: str
    name: str
    icon: str | None = None
    accent: str = "#5b8cff"
    platform: str | None = None       # "windows" / "linux" / None = everywhere
    keys: list[Slot] = field(default_factory=lambda: [Slot() for _ in range(KEYS)])
    zones: list[Slot] = field(default_factory=lambda: [Slot() for _ in range(ZONES)])
    knobs: list[Knob] = field(default_factory=lambda: [Knob() for _ in range(KNOBS)])
    swipe_left: Action | None = None
    swipe_right: Action | None = None
    effects: list[str] = field(default_factory=list)   # e.g. "ac_shift_lights"
    style: dict[str, Any] = field(default_factory=dict)  # defaults for every slot of this page

    @classmethod
    def new(cls, name: str) -> "Page":
        return cls(id=uuid.uuid4().hex[:8], name=name)

    @classmethod
    def from_dict(cls, d: dict) -> "Page":
        def pad(items, n, factory):
            items = list(items or [])[:n]
            return [factory(x) for x in items] + [factory(None) for _ in range(n - len(items))]
        swipe = d.get("swipe") or {}
        return cls(id=d.get("id") or uuid.uuid4().hex[:8], name=d.get("name", "Page"),
                   icon=d.get("icon"), accent=d.get("accent", "#5b8cff"), platform=d.get("platform"),
                   keys=pad(d.get("keys"), KEYS, Slot.from_dict),
                   zones=pad(d.get("zones"), ZONES, Slot.from_dict),
                   knobs=pad(d.get("knobs"), KNOBS, Knob.from_dict),
                   swipe_left=swipe.get("left"), swipe_right=swipe.get("right"),
                   effects=list(d.get("effects") or []), style=dict(d.get("style") or {}))

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"id": self.id, "name": self.name, "accent": self.accent}
        if self.icon:
            d["icon"] = self.icon
        if self.platform:
            d["platform"] = self.platform
        d["keys"] = [s.to_dict() for s in self.keys]
        d["zones"] = [s.to_dict() for s in self.zones]
        d["knobs"] = [k.to_dict() for k in self.knobs]
        swipe = {k: v for k, v in (("left", self.swipe_left), ("right", self.swipe_right)) if v}
        if swipe:
            d["swipe"] = swipe
        if self.effects:
            d["effects"] = self.effects
        if self.style:
            d["style"] = self.style
        return d

    def available_on(self, platform: str) -> bool:
        return self.platform in (None, "", platform)


@dataclass
class Profile:
    name: str = "FreeDock"
    brightness: int = 70
    start_page: str | None = None
    pages: list[Page] = field(default_factory=list)
    auto_switch: list[dict[str, str]] = field(default_factory=list)   # {"process": "acs.exe", "page": id}
    idle_minutes: int = 0             # 0 = never
    idle_mode: str = "clock"          # clock | aurora | off
    animations: bool = True
    theme: dict[str, Any] = field(default_factory=dict)       # defaults for every slot of every page
    seen_presets: list[str] = field(default_factory=list)     # built-in pages already offered
    path: Path | None = None

    # --------------------------------------------------------------- lookup
    def page(self, page_id: str | None) -> Page | None:
        return next((p for p in self.pages if p.id == page_id), None)

    def page_index(self, page_id: str) -> int:
        return next((i for i, p in enumerate(self.pages) if p.id == page_id), 0)

    # --------------------------------------------------------------- io
    @classmethod
    def from_dict(cls, d: dict) -> "Profile":
        idle = d.get("idle") or {}
        return cls(name=d.get("name", "FreeDock"), brightness=int(d.get("brightness", 70)),
                   start_page=d.get("start_page"), pages=[Page.from_dict(p) for p in d.get("pages", [])],
                   auto_switch=list(d.get("auto_switch") or []),
                   idle_minutes=int(idle.get("minutes", 0)), idle_mode=idle.get("mode", "clock"),
                   animations=bool(d.get("animations", True)), theme=dict(d.get("theme") or {}),
                   # profiles from 0.1 had exactly these built-in pages
                   seen_presets=list(d["seen_presets"] if "seen_presets" in d
                                     else ["daily", "windows", "assetto", "msfs", "msfs2"]))

    def to_dict(self) -> dict:
        return {"schema": SCHEMA, "name": self.name, "brightness": self.brightness,
                "start_page": self.start_page, "animations": self.animations,
                "idle": {"minutes": self.idle_minutes, "mode": self.idle_mode},
                "theme": self.theme, "seen_presets": self.seen_presets,
                "auto_switch": self.auto_switch, "pages": [p.to_dict() for p in self.pages]}

    @classmethod
    def load(cls, path: Path) -> "Profile":
        prof = cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
        prof.path = path
        return prof

    def save(self, path: Path | None = None) -> Path:
        path = path or self.path or (config_dir() / "profile.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
        self.path = path
        return path


def default_profile_path() -> Path:
    return config_dir() / "profile.json"


def load_or_create(language: str) -> Profile:
    path = default_profile_path()
    if path.is_file():
        try:
            prof = Profile.load(path)
        except (OSError, ValueError, KeyError, TypeError):
            path.replace(path.with_suffix(".broken.json"))
        else:
            from .presets import add_new_presets
            if add_new_presets(prof, language):
                prof.save(path)
            return prof
    from .presets import build_default_profile
    prof = build_default_profile(language)
    prof.save(path)
    return prof
