"""Deck pages for the games of 0.5: Assetto Corsa EVO, Motor Town, Street Legal Racing Redline, New Star GP,
Need for Speed Heat and City Car Driving 2.0.

Keys fire each game's own default keyboard bindings (checked in the games' config files: Motor Town's
GameUserSettings.ini, SLRR's control set, New Star GP's UserData.txt, City Car Driving's binding preset,
the official NFS Heat manual). AC EVO's bindings live in a binary file, so its page is the dashboard only.
Registered in presets.NEW_PAGES, so existing profiles get them once, with their auto-switch rule.
"""
from __future__ import annotations

from .presets import (L, NEW_PAGES, NEW_RULES, PAGE_NAME_ZONE_STYLE, SLATE, _nav, _t, hk, st, w)
from .profile import Knob, Page, Slot

L.update({
    "acevo": {"en": "AC EVO", "pt": "AC EVO", "es": "AC EVO"},
    "motortown": {"en": "Motor Town", "pt": "Motor Town", "es": "Motor Town"},
    "slrr": {"en": "SLRR", "pt": "SLRR", "es": "SLRR"},
    "nsgp": {"en": "New Star GP", "pt": "New Star GP", "es": "New Star GP"},
    "nfsheat": {"en": "NFS Heat", "pt": "NFS Heat", "es": "NFS Heat"},
    "ccd2": {"en": "City Car 2", "pt": "City Car 2", "es": "City Car 2"},
    "starter": {"en": "Starter", "pt": "Partida", "es": "Arranque"},
    "map": {"en": "Map", "pt": "Mapa", "es": "Mapa"},
    "reset": {"en": "Reset", "pt": "Resetar", "es": "Reiniciar"},
    "interact": {"en": "Interact", "pt": "Interagir", "es": "Interactuar"},
    "autopilot": {"en": "AI drive", "pt": "Piloto IA", "es": "Piloto IA"},
    "jake": {"en": "Jake brake", "pt": "Freio motor", "es": "Freno motor"},
    "nitro": {"en": "Nitro", "pt": "Nitro", "es": "Nitro"},
    "look_back": {"en": "Look back", "pt": "Olhar trás", "es": "Mirar atrás"},
    "music": {"en": "Music", "pt": "Música", "es": "Música"},
    "cruise": {"en": "Cruise", "pt": "Cruzeiro", "es": "Crucero"},
    "boost": {"en": "Boost", "pt": "Boost", "es": "Boost"},
    "rewind": {"en": "Rewind", "pt": "Rebobinar", "es": "Rebobinar"},
    "respawn": {"en": "Respawn", "pt": "Voltar à pista", "es": "Volver a pista"},
    "leave_pit": {"en": "Leave pit", "pt": "Sair do box", "es": "Salir de boxes"},
    "fuel": {"en": "Fuel", "pt": "Combustível", "es": "Combustible"},
    "line": {"en": "Racing line", "pt": "Linha", "es": "Trazada"},
    "phone": {"en": "Phone", "pt": "Celular", "es": "Móvil"},
    "action": {"en": "Action", "pt": "Ação", "es": "Acción"},
    "seatbelt": {"en": "Seatbelt", "pt": "Cinto", "es": "Cinturón"},
    "navigator": {"en": "GPS", "pt": "GPS", "es": "GPS"},
    "fog": {"en": "Fog lights", "pt": "Neblina", "es": "Antiniebla"},
    "screenshot": {"en": "Screenshot", "pt": "Captura", "es": "Captura"},
    "siren": {"en": "Siren", "pt": "Sirene", "es": "Sirena"},
    "times": {"en": "Times", "pt": "Tempos", "es": "Tiempos"},
    "objectives": {"en": "Objectives", "pt": "Objetivos", "es": "Objetivos"},
})

WARM = st("#3a2a05", "#1a1302", "#facc15")
RED = st("#3a0d14", "#1a0609", "#ef4444")
GREEN = st("#14261a", "#08120c", "#22c55e")
ORANGE = st("#3a1d05", "#1a0d02", "#f97316")
BLUE_ = st("#0c2d48", "#06182a", "#38bdf8")
VOLUME = Knob(left={"type": "volume", "op": "down", "step": 2}, right={"type": "volume", "op": "up", "step": 2},
              press={"type": "volume", "op": "mute"})


def _zone_name() -> Slot:
    return Slot(widget=w("page_name"), style=PAGE_NAME_ZONE_STYLE, action={"type": "page", "target": "menu"})


def acevo(lang: str) -> Page:
    """AC EVO: the shared-memory dashboard (gear, speed, rpm, fuel, TC, ABS, pit limiter). Buttons are in the
    game's own mapping (wheel and button box), so the deck shows instead of pressing."""
    p = Page(id="acevo", name=_t(lang, "acevo"), icon="car", accent="#dc2626",
             effects=["ac_shift_lights", "rgb_shift_lights"], style={"change": "zoom"})
    dark = st("#0a0a0a", "#171717")
    p.keys = [
        Slot(widget=w("ac", metric="gear"), style=st("#000000", "#111111", "#22c55e")),
        Slot(widget=w("ac", metric="speed"), style=dark),
        Slot(widget=w("ac", metric="rpm"), style=dark),
        Slot(widget=w("ac", metric="fuel"), style=st("#1a1405", "#0d0a02", "#f59e0b")),
        Slot(widget=w("ac", metric="pit_limiter"), style=ORANGE),
        Slot(widget=w("ac", metric="tyres"), style=dark),
        Slot(widget=w("volume"), style=st("#14213d", "#0a1020")),
        Slot(widget=w("mic"), style=st("#1c1917", "#0c0a09")),
        Slot(label=_t(lang, "screenshot"), icon="camera", action=hk("win+alt+printscreen"), style=SLATE),
        Slot(label=_t(lang, "music"), icon="play", action={"type": "media", "op": "playpause"}, style=SLATE),
    ]
    p.zones = [Slot(widget=w("clock"), style=dark), Slot(widget=w("volume"), style=st("#14213d", "#0a1020")),
               Slot(widget=w("ac", metric="gear"), style=dark), _zone_name()]
    p.knobs = [VOLUME, Knob(left={"type": "media", "op": "prevtrack"}, right={"type": "media", "op": "nexttrack"},
                            press={"type": "media", "op": "playpause"}), Knob(), _nav("acevo")]
    return p


def motortown(lang: str) -> Page:
    p = Page(id="motortown", name=_t(lang, "motortown"), icon="truck", accent="#0ea5e9",
             effects=["rgb_shift_lights"], style={"change": "zoom"})
    dash = st("#04121c", "#0a1d2b", "#0ea5e9")
    p.keys = [
        Slot(widget=w("motortown", metric="speed"), style=dash),
        Slot(widget=w("motortown", metric="gear"), style=st("#000000", "#111111", "#22c55e")),
        Slot(widget=w("motortown", metric="fuel"), style=st("#1a1405", "#0d0a02", "#f59e0b")),
        Slot(widget=w("motortown", metric="money"), style=dash),
        Slot(label=_t(lang, "starter"), icon="power", action=hk("e"), style=GREEN),
        Slot(label=_t(lang, "headlights"), icon="lightbulb", action=hk("4"), long_action=hk("shift+4"),
             widget=w("motortown", metric="lights"), style=WARM),
        Slot(label=_t(lang, "hazards"), icon="triangle-alert", action=hk("3"),
             widget=w("motortown", metric="hazard"), style=ORANGE),
        Slot(label=_t(lang, "interact"), icon="hand", action=hk("x"), long_action=hk("z"), style=BLUE_),
        Slot(label=_t(lang, "autopilot"), icon="bot", action=hk("5"), style=st("#1e1033", "#0e0819", "#a855f7")),
        Slot(label=_t(lang, "map"), icon="map", action=hk("m"), long_action=hk("tab"), style=SLATE),
    ]
    p.zones = [
        Slot(widget=w("motortown", metric="cruise"), style=dash),
        Slot(label=_t(lang, "jake"), icon="gauge", action=hk("b"), style=RED),
        Slot(label=_t(lang, "reset"), icon="rotate-ccw", long_action=hk("r"), style=SLATE),
        _zone_name(),
    ]
    p.knobs = [
        Knob(left=hk("1"), right=hk("2"), press=hk("h")),              # blinkers, horn
        Knob(left=hk("pagedown"), right=hk("pageup"), press=hk("5")),  # cruise speed, AI drive
        Knob(left=hk("comma"), right=hk("period"), press=hk("c")),     # look, camera
        _nav("motortown"),
    ]
    return p


def slrr(lang: str) -> Page:
    p = Page(id="slrr", name=_t(lang, "slrr"), icon="car-front", accent="#f97316", style={"change": "zoom"})
    p.keys = [
        Slot(label=_t(lang, "nitro"), icon="flame", action=hk("n"), style=BLUE_),
        Slot(label=_t(lang, "horn"), icon="megaphone", action=hk("h"), style=SLATE),
        Slot(label=_t(lang, "cruise"), icon="gauge", action=hk("s"), style=GREEN),
        Slot(label=_t(lang, "look_back"), icon="rotate-ccw", action=hk("f4"), style=SLATE),
        Slot(label=_t(lang, "pause"), icon="pause", action=hk("esc"), style=RED),
        Slot(label="F1", icon="armchair", action=hk("f1"), style=SLATE),
        Slot(label="F2", icon="tv", action=hk("f2"), style=SLATE),
        Slot(label="F3", icon="car", action=hk("f3"), style=SLATE),
        Slot(label="F5", icon="video", action=hk("f5"), style=SLATE),
        Slot(label=_t(lang, "screenshot"), icon="camera", action=hk("f12"), style=SLATE),
    ]
    p.zones = [Slot(widget=w("clock"), style=SLATE), Slot(widget=w("volume"), style=st("#14213d", "#0a1020")),
               Slot(label=_t(lang, "music"), icon="music", action=hk("period"), long_action=hk("comma"), style=SLATE),
               _zone_name()]
    p.knobs = [Knob(left=hk("pageup"), right=hk("pagedown"), press=hk("f1")),       # look around, cockpit
               Knob(left=hk("comma"), right=hk("period")), VOLUME, _nav("slrr")]
    return p


def nsgp(lang: str) -> Page:
    p = Page(id="nsgp", name=_t(lang, "nsgp"), icon="flag", accent="#facc15", style={"change": "zoom"})
    p.keys = [
        Slot(label=_t(lang, "boost"), icon="zap", action=hk("num0"), style=BLUE_),
        Slot(label=_t(lang, "rewind"), icon="rewind", action=hk("r"), style=st("#1e1033", "#0e0819", "#a855f7")),
        Slot(label=_t(lang, "respawn"), icon="rotate-ccw", action=hk("tab"), style=SLATE),
        Slot(label=_t(lang, "leave_pit"), icon="log-out", action=hk("l"), style=GREEN),
        Slot(label=_t(lang, "pause"), icon="pause", action=hk("esc"), style=RED),
        Slot(label=_t(lang, "line"), icon="spline", action=hk("g"), style=SLATE),
        Slot(label=_t(lang, "camera"), icon="camera", action=hk("2"), long_action=hk("1"), style=SLATE),
        Slot(label=_t(lang, "times"), icon="timer", action=hk("shift"), style=SLATE),
        Slot(label=_t(lang, "fuel") + " +", icon="fuel", action=hk("u"), style=WARM),
        Slot(label=_t(lang, "fuel") + " −", icon="fuel", action=hk("y"), style=WARM),
    ]
    p.zones = [Slot(widget=w("clock"), style=SLATE), Slot(widget=w("volume"), style=st("#14213d", "#0a1020")),
               Slot(label=_t(lang, "rewind"), icon="history", action=hk("r"), style=SLATE), _zone_name()]
    p.knobs = [Knob(left=hk("q"), right=hk("e"), press=hk("r")),       # rewind back / forward
               Knob(left=hk("y"), right=hk("u")),                       # fuel
               Knob(left=hk("1"), right=hk("2")),                       # camera
               _nav("nsgp")]
    return p


def nfsheat(lang: str) -> Page:
    p = Page(id="nfsheat", name=_t(lang, "nfsheat"), icon="flame", accent="#ec4899", style={"change": "zoom"})
    pink = st("#2a0a1f", "#14050f", "#ec4899")
    p.keys = [
        Slot(label=_t(lang, "nitro"), icon="flame", action=hk("ctrl"), style=BLUE_),
        Slot(label=_t(lang, "horn"), icon="megaphone", action=hk("h"), style=SLATE),
        Slot(label=_t(lang, "action"), icon="hand", action=hk("b"), style=pink),
        Slot(label=_t(lang, "camera"), icon="camera", action=hk("c"), style=SLATE),
        Slot(label=_t(lang, "map"), icon="map", action=hk("m"), style=SLATE),
        Slot(label=_t(lang, "phone"), icon="smartphone", action=hk("num6"), style=pink),
        Slot(label=_t(lang, "objectives"), icon="list-checks", action=hk("num4"), style=SLATE),
        Slot(label=_t(lang, "music"), icon="skip-forward", action=hk("num2"), style=SLATE),
        Slot(label=_t(lang, "screenshot"), icon="aperture", action=hk("printscreen"), style=SLATE),
        Slot(label=_t(lang, "pause"), icon="pause", action=hk("tab"), style=RED),
    ]
    p.zones = [Slot(widget=w("clock"), style=SLATE), Slot(widget=w("volume"), style=st("#14213d", "#0a1020")),
               Slot(label=_t(lang, "nitro"), icon="flame", action=hk("ctrl"), style=BLUE_), _zone_name()]
    p.knobs = [VOLUME, Knob(left={"type": "media", "op": "prevtrack"}, right={"type": "media", "op": "nexttrack"},
                            press={"type": "media", "op": "playpause"}), Knob(left=hk("l"), right=hk("o")),
               _nav("nfsheat")]
    return p


def ccd2(lang: str) -> Page:
    p = Page(id="ccd2", name=_t(lang, "ccd2"), icon="car", accent="#a855f7", style={"change": "zoom"})
    p.keys = [
        Slot(label=_t(lang, "ignition"), icon="power", action=hk("e"), style=GREEN),
        Slot(label=_t(lang, "seatbelt"), icon="shield-check", action=hk("g"), style=SLATE),
        Slot(label=_t(lang, "parking"), icon="circle-parking", action=hk("b"), style=RED),
        Slot(label=_t(lang, "headlights"), icon="lightbulb", action=hk("l"), long_action=hk("k"), style=WARM),
        Slot(label=_t(lang, "hazards"), icon="triangle-alert", action=hk("i"), style=ORANGE),
        Slot(label=_t(lang, "wipers"), icon="cloud-rain", action=hk("h"), long_action=hk("u"), style=BLUE_),
        Slot(label=_t(lang, "fog"), icon="cloud-fog", action=hk("p"), long_action=hk("o"), style=SLATE),
        Slot(label=_t(lang, "navigator"), icon="map", action=hk("3"), style=SLATE),
        Slot(label=_t(lang, "phone"), icon="smartphone", action=hk("4"), style=SLATE),
        Slot(label=_t(lang, "interact"), icon="hand", action=hk("n"), style=st("#1e1033", "#0e0819", "#a855f7")),
    ]
    p.zones = [Slot(label=_t(lang, "flash"), icon="zap", action=hk("j"), style=WARM),
               Slot(label="TCS / ABS", icon="gauge", action=hk("delete"), long_action=hk("insert"), style=SLATE),
               Slot(label=_t(lang, "camera"), icon="camera", action=hk("v"), style=SLATE), _zone_name()]
    p.knobs = [Knob(left=hk("comma"), right=hk("period"), press=hk("f")),   # blinkers, horn
               Knob(left=hk("minus"), right=hk("equal"), press=hk("0")),     # radio
               Knob(left=hk("a"), right=hk("d"), press=hk("v")),             # look, camera
               _nav("ccd2")]
    return p


NEW_PAGES.update({"acevo": acevo, "motortown": motortown, "slrr": slrr, "nsgp": nsgp, "nfsheat": nfsheat,
                  "ccd2": ccd2})
NEW_RULES += [{"process": "AssettoCorsaEVO.exe", "page": "acevo"},
              {"process": "MotorTown-Win64-Shipping.exe", "page": "motortown"},
              {"process": "StreetLegal_Redline.exe", "page": "slrr"},
              {"process": "NSGP.exe", "page": "nsgp"},
              {"process": "NeedForSpeedHeat.exe", "page": "nfsheat"},
              {"process": "CityCarDriving2-Win64-Shipping.exe", "page": "ccd2"}]


# --- EA SPORTS FC 26: a gamepad game. The deck drives IAmPcGamer (screenshot, overlay panel, hide or
# return the wheel) through its global hotkeys, plus the Xbox Game Bar and the music.
L.update({
    "fc26": {"en": "FC 26", "pt": "FC 26", "es": "FC 26"},
    "pg_panel": {"en": "Assistant", "pt": "Assistente", "es": "Asistente"},
    "pg_wheel": {"en": "Wheel on/off", "pt": "Volante on/off", "es": "Volante on/off"},
    "gamebar": {"en": "Game Bar", "pt": "Game Bar", "es": "Game Bar"},
    "clip": {"en": "Record", "pt": "Gravar", "es": "Grabar"},
    "last30": {"en": "Last 30 s", "pt": "Últimos 30 s", "es": "Últimos 30 s"},
})
PG_CAPTURE, PG_PANEL, PG_WHEEL = "ctrl+alt+f12", "ctrl+alt+f11", "ctrl+alt+f10"   # IAmPcGamer defaults


def fc26(lang: str) -> Page:
    p = Page(id="fc26", name=_t(lang, "fc26"), icon="trophy", accent="#22c55e", style={"change": "zoom"})
    violet = st("#1e1033", "#0e0819", "#8b5cf6")
    p.keys = [
        Slot(label=_t(lang, "screenshot"), icon="camera", action=hk(PG_CAPTURE), style=violet),
        Slot(label=_t(lang, "pg_panel"), icon="brain", action=hk(PG_PANEL), style=violet),
        Slot(label=_t(lang, "pg_wheel"), icon="eye-off", action=hk(PG_WHEEL), style=violet),
        Slot(label=_t(lang, "pause"), icon="pause", action=hk("esc"), style=RED),
        Slot(label=_t(lang, "gamebar"), icon="gamepad-2", action=hk("win+g"), style=GREEN),
        Slot(label=_t(lang, "clip"), icon="circle-dot", action=hk("win+alt+r"), style=RED),
        Slot(label=_t(lang, "last30"), icon="history", action=hk("win+alt+g"), style=GREEN),
        Slot(label=_t(lang, "music"), icon="play", action={"type": "media", "op": "playpause"}, style=SLATE),
        Slot(icon="skip-back", action={"type": "media", "op": "prevtrack"}, style=SLATE),
        Slot(icon="skip-forward", action={"type": "media", "op": "nexttrack"}, style=SLATE),
    ]
    p.zones = [Slot(widget=w("clock"), style=SLATE), Slot(widget=w("volume"), style=st("#14213d", "#0a1020")),
               Slot(label=_t(lang, "screenshot"), icon="camera", action=hk(PG_CAPTURE), style=violet), _zone_name()]
    p.knobs = [VOLUME, Knob(left={"type": "media", "op": "prevtrack"}, right={"type": "media", "op": "nexttrack"},
                            press={"type": "media", "op": "playpause"}), Knob(press=hk(PG_PANEL)), _nav("fc26")]
    return p


NEW_PAGES["fc26"] = fc26
NEW_RULES += [{"process": "FC26.exe", "page": "fc26"}]
