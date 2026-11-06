"""Looks: every customisable property of a key, and how the three levels combine.

A slot's look comes from three dictionaries, each overriding the one before it:

    profile.theme  ->  page.style  ->  slot.style

So a whole profile can switch fonts or animations in one place, a page can have its own colour
scheme, and any single key can still be different. All values are plain JSON so profiles can be
shared. The editor builds its controls from :data:`OPTIONS`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .render.painter import ANIMATIONS


@dataclass(frozen=True)
class Option:
    key: str
    kind: str                 # color | choice | float | bool | font
    default: Any = None
    choices: tuple = ()       # for choice: values (labels come from locales "style.<key>.<value>")
    lo: float = 0.0
    hi: float = 1.0


ANIM_CHOICES = ("none",) + tuple(ANIMATIONS)
CHANGE_CHOICES = ("none", "fade", "slide", "zoom")

OPTIONS: tuple[Option, ...] = (
    Option("bg", "color"),
    Option("bg2", "color"),
    Option("grad", "choice", "diag", ("diag", "vertical", "horizontal", "radial")),
    Option("accent", "color"),
    Option("accent_lock", "bool", False),       # keep my accent even when a widget wants another
    Option("fg", "color"),
    Option("icon_color", "color"),
    Option("font", "font"),
    Option("font_value", "font"),
    Option("font_scale", "float", 1.0, lo=0.5, hi=1.8),
    Option("bold", "bool", True),
    Option("icon_scale", "float", 1.0, lo=0.4, hi=1.5),
    Option("radius", "float", 1.0, lo=0.0, hi=2.5),
    Option("border", "choice", "auto", ("auto", "none", "accent")),
    Option("sheen", "bool", True),
    Option("image_fit", "choice", "cover", ("cover", "contain", "stretch")),
    Option("image_dim", "float", 0.0, lo=0.0, hi=0.8),
    Option("anim", "choice", "none", ANIM_CHOICES),
    Option("anim_when", "choice", "always", ("always", "active", "idle")),
    Option("anim_speed", "float", 1.0, lo=0.25, hi=3.0),
    Option("active_anim", "choice", "glow", ANIM_CHOICES),   # used while a toggle/widget is "on"
    Option("change", "choice", "fade", CHANGE_CHOICES),      # transition when the content changes
)
BY_KEY = {o.key: o for o in OPTIONS}

# Built-in defaults under every theme: what FreeDock looks like out of the box.
BASE = {"bg": "#161a24", "fg": "#f5f7fb", "active_anim": "glow", "change": "fade"}

# Visual fields that map 1:1 from a style key
_VISUAL_KEYS = ("bg", "bg2", "fg", "icon_color", "font", "font_value", "grad", "image_fit", "border")
_VISUAL_FLOATS = ("font_scale", "icon_scale", "radius", "image_dim")
_VISUAL_BOOLS = ("bold", "sheen")


def merged(*layers: dict | None) -> dict:
    out = dict(BASE)
    for layer in layers:
        if layer:
            out.update({k: v for k, v in layer.items() if v not in (None, "")})
    return out


def visual_kwargs(style: dict) -> dict:
    """The :class:`Visual` constructor arguments a merged style asks for."""
    kw: dict[str, Any] = {}
    for k in _VISUAL_KEYS:
        if style.get(k):
            kw[k] = style[k]
    for k in _VISUAL_FLOATS:
        if k in style:
            try:
                kw[k] = float(style[k])
            except (TypeError, ValueError):
                pass
    for k in _VISUAL_BOOLS:
        if k in style:
            kw[k] = bool(style[k])
    if style.get("accent"):
        kw["accent"] = style["accent"]
    return kw


DEMANDS = {"alert"}      # widget animations that override the user's choice (they need attention)


def animation_for(style: dict, active: bool, widget_anim: str | None) -> str | None:
    """Which animation a slot plays right now.

    A widget may suggest one (weather icon floats, a spinner while an AI works); the user's own
    ``anim`` setting - even "none" - wins over suggestions, but never over an attention demand.
    """
    if widget_anim in DEMANDS:
        return widget_anim
    if widget_anim and "anim" not in style:
        return widget_anim
    anim = style.get("anim") or "none"
    when = style.get("anim_when") or "always"
    if anim != "none" and (when == "always" or (when == "active") == active):
        return anim
    if active:
        a = style.get("active_anim") or "none"
        return None if a == "none" else a
    return None


def period(anim: str, style: dict) -> float:
    try:
        speed = max(0.1, float(style.get("anim_speed", 1.0)))
    except (TypeError, ValueError):
        speed = 1.0
    return ANIMATIONS.get(anim, 2.0) / speed
