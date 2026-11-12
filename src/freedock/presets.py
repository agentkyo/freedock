"""The profile FreeDock ships with: Daily, Windows, Assetto Corsa and Flight Simulator pages."""
from __future__ import annotations

from PySide6.QtGui import QColor

from .profile import Knob, Page, Profile, Slot

L = {
    "daily": {"en": "Daily", "pt": "Dia a dia", "es": "Día a día"},
    "windows": {"en": "Windows", "pt": "Windows", "es": "Windows"},
    "assetto": {"en": "Assetto Corsa", "pt": "Assetto Corsa", "es": "Assetto Corsa"},
    "msfs": {"en": "Flight Sim · Autopilot", "pt": "Flight Sim · Piloto auto", "es": "Flight Sim · Piloto auto"},
    "msfs2": {"en": "Flight Sim · Flight", "pt": "Flight Sim · Voo", "es": "Flight Sim · Vuelo"},
    "prev": {"en": "Previous", "pt": "Anterior", "es": "Anterior"},
    "play": {"en": "Play/Pause", "pt": "Play/Pause", "es": "Play/Pausa"},
    "next": {"en": "Next", "pt": "Próxima", "es": "Siguiente"},
    "deck": {"en": "Deck light", "pt": "Brilho", "es": "Brillo"},
    "scroll": {"en": "Scroll", "pt": "Rolagem", "es": "Desplazar"},
    "taskmgr": {"en": "Task Mgr", "pt": "Gerenciador", "es": "Tareas"},
    "explorer": {"en": "Files", "pt": "Arquivos", "es": "Archivos"},
    "settings": {"en": "Settings", "pt": "Configurações", "es": "Ajustes"},
    "clipboard": {"en": "Clipboard", "pt": "Área transf.", "es": "Portapapeles"},
    "emoji": {"en": "Emoji", "pt": "Emoji", "es": "Emoji"},
    "desktop": {"en": "Desktop", "pt": "Área de trab.", "es": "Escritorio"},
    "snip": {"en": "Snip", "pt": "Recorte", "es": "Recorte"},
    "lock": {"en": "Lock", "pt": "Bloquear", "es": "Bloquear"},
    "run": {"en": "Run", "pt": "Executar", "es": "Ejecutar"},
    "taskview": {"en": "Task view", "pt": "Visão tarefas", "es": "Vista tareas"},
    "vdesk": {"en": "Desktops", "pt": "Áreas", "es": "Escritorios"},
    "zoom": {"en": "Zoom", "pt": "Zoom", "es": "Zoom"},
    "snap": {"en": "Snap", "pt": "Encaixar", "es": "Ajustar"},
    "headlights": {"en": "Lights", "pt": "Farol", "es": "Luces"},
    "flash": {"en": "Flash", "pt": "Lampejo", "es": "Ráfaga"},
    "horn": {"en": "Horn", "pt": "Buzina", "es": "Bocina"},
    "hazards": {"en": "Hazards", "pt": "Alerta", "es": "Emergencia"},
    "wipers": {"en": "Wipers", "pt": "Limpador", "es": "Limpiaparabrisas"},
    "tc": {"en": "TC", "pt": "TC", "es": "TC"},
    "gear": {"en": "Gear", "pt": "Trem", "es": "Tren"},
    "flaps_up": {"en": "Flaps ▲", "pt": "Flaps ▲", "es": "Flaps ▲"},
    "flaps_dn": {"en": "Flaps ▼", "pt": "Flaps ▼", "es": "Flaps ▼"},
    "park": {"en": "Park brake", "pt": "Freio estac.", "es": "Freno estac."},
    "landing": {"en": "Landing lt", "pt": "Luz pouso", "es": "Luz aterr."},
    "pause": {"en": "Pause", "pt": "Pausa", "es": "Pausa"},
    "autostart": {"en": "Auto start", "pt": "Partida auto", "es": "Arranque auto"},
    "beacon": {"en": "Beacon", "pt": "Beacon", "es": "Beacon"},
    "navlt": {"en": "Nav lights", "pt": "Luz nav", "es": "Luz nav"},
    "taxi": {"en": "Taxi light", "pt": "Luz táxi", "es": "Luz taxi"},
    "strobe": {"en": "Strobe", "pt": "Strobe", "es": "Strobe"},
    "on_ground": {"en": "On ground", "pt": "No solo", "es": "En tierra"},
}


def _t(lang: str, key: str) -> str:
    return L[key].get(lang) or L[key]["en"]


def st(bg: str, bg2: str | None = None, accent: str | None = None, fg: str | None = None) -> dict:
    d = {"bg": bg}
    if bg2:
        d["bg2"] = bg2
    if accent:
        d["accent"] = accent
    if fg:
        d["fg"] = fg
    return d


def hk(keys: str) -> dict:
    return {"type": "hotkey", "keys": keys}


def ac(binding: str) -> dict:
    return {"type": "ac_binding", "binding": binding}


def ev(event: str) -> dict:
    return {"type": "sim_event", "event": event}


def w(kind: str, **cfg) -> dict:
    return {"type": kind, **cfg}


BLUE = st("#1d2b53", "#0b1020")
SLATE = st("#1e2533", "#0d1118")


AUDIO_OUT_STYLE = st("#062a33", "#03161b", "#22d3ee")


def audio_output_zone() -> Slot:
    """Default playback device; tap = next one (Ctrl+Win+V on the deck)."""
    return Slot(widget=w("audio_output"), style=AUDIO_OUT_STYLE)


def audio_output_knob() -> Knob:
    return Knob(left={"type": "audio_output", "op": "prev"}, right={"type": "audio_output", "op": "next"},
                press={"type": "audio_output", "op": "next"})


def daily(lang: str) -> Page:
    p = Page(id="daily", name=_t(lang, "daily"), icon="sun", accent="#5b8cff")
    p.keys = [
        Slot(widget=w("clock", format="24h", date=True), style=st("#111827", "#1f2937")),
        Slot(widget=w("weather"), style=st("#0c4a6e", "#082f49", "#38bdf8")),
        Slot(widget=w("cpu"), style=st("#0f2a1d", "#07130d")),
        Slot(widget=w("gpu"), style=st("#0f2a1d", "#07130d")),
        Slot(widget=w("ram"), style=st("#0f2a1d", "#07130d")),
        Slot(label=_t(lang, "prev"), icon="skip-back", action={"type": "media", "op": "prevtrack"}, style=BLUE),
        Slot(label=_t(lang, "play"), icon="play", action={"type": "media", "op": "playpause"},
             style=st("#3b1d6e", "#160b2e", "#a855f7")),
        Slot(label=_t(lang, "next"), icon="skip-forward", action={"type": "media", "op": "nexttrack"}, style=BLUE),
        Slot(widget=w("mic"), style=st("#1c1917", "#0c0a09")),
        Slot(label="Pomodoro", widget=w("pomodoro", minutes=25), long_action={"type": "timer", "op": "reset"},
             style=st("#2a1240", "#12081c", "#c084fc")),
    ]
    p.zones = [
        Slot(widget=w("volume"), style=st("#14213d", "#0a1020")),
        audio_output_zone(),
        Slot(label=_t(lang, "scroll"), icon="mouse", style=SLATE),
        Slot(widget=w("page_name"), style=st("#1e1b4b", "#0b0a1f", "#818cf8"), action={"type": "page", "target": "menu"}),
    ]
    p.knobs = [
        Knob(left={"type": "volume", "op": "down", "step": 2}, right={"type": "volume", "op": "up", "step": 2},
             press={"type": "volume", "op": "mute"}),
        audio_output_knob(),
        Knob(left={"type": "scroll", "steps": 3}, right={"type": "scroll", "steps": -3},
             press={"type": "media", "op": "playpause"}),
        Knob(left={"type": "page", "target": "prev"}, right={"type": "page", "target": "next"},
             press={"type": "page", "target": "daily"}),
    ]
    return p


def windows(lang: str) -> Page:
    p = Page(id="windows", name=_t(lang, "windows"), icon="app-window", accent="#0ea5e9", platform="windows")
    c = st("#0c2d48", "#06182a")
    p.keys = [
        Slot(label=_t(lang, "taskmgr"), icon="activity", action=hk("ctrl+shift+esc"), style=st("#3a0d14", "#1a0609", "#f43f5e")),
        Slot(label=_t(lang, "explorer"), icon="folder-open", action=hk("win+e"), style=st("#3a2a05", "#1a1302", "#facc15")),
        Slot(label=_t(lang, "settings"), icon="settings", action=hk("win+i"), style=c),
        Slot(label=_t(lang, "clipboard"), icon="clipboard-list", action=hk("win+v"), style=c),
        Slot(label=_t(lang, "emoji"), icon="smile", action=hk("win+period"), style=st("#3a2a05", "#1a1302", "#facc15")),
        Slot(label=_t(lang, "desktop"), icon="monitor", action=hk("win+d"), style=c),
        Slot(label=_t(lang, "snip"), icon="scissors", action=hk("win+shift+s"), style=st("#2a1240", "#12081c", "#c084fc")),
        Slot(label=_t(lang, "lock"), icon="lock", action=hk("win+l"), style=st("#3a0d14", "#1a0609", "#f43f5e")),
        Slot(label=_t(lang, "run"), icon="square-terminal", action=hk("win+r"), style=c),
        Slot(label=_t(lang, "taskview"), icon="layout-grid", action=hk("win+tab"), style=c),
    ]
    p.zones = [
        Slot(label=_t(lang, "vdesk"), icon="panels-top-left", style=SLATE, action=hk("win+tab")),
        Slot(label=_t(lang, "zoom"), icon="zoom-in", style=SLATE, action=hk("ctrl+0")),
        Slot(label=_t(lang, "snap"), icon="app-window", style=SLATE, action=hk("win+up")),
        Slot(widget=w("page_name"), style=st("#1e1b4b", "#0b0a1f", "#818cf8"), action={"type": "page", "target": "menu"}),
    ]
    p.knobs = [
        Knob(left=hk("ctrl+win+left"), right=hk("ctrl+win+right"), press=hk("win+tab")),
        Knob(left=hk("ctrl+minus"), right=hk("ctrl+equal"), press=hk("ctrl+0")),
        Knob(left=hk("win+left"), right=hk("win+right"), press=hk("win+up")),
        Knob(left={"type": "page", "target": "prev"}, right={"type": "page", "target": "next"},
             press={"type": "page", "target": "daily"}),
    ]
    return p


def assetto(lang: str) -> Page:
    p = Page(id="assetto", name=_t(lang, "assetto"), icon="car", accent="#ef4444",
             effects=["ac_shift_lights", "rgb_shift_lights"], style={"change": "zoom"})
    dark = st("#0a0a0a", "#171717")
    p.keys = [
        Slot(widget=w("ac", metric="gear"), style=st("#000000", "#111111", "#22c55e")),
        Slot(widget=w("ac", metric="lap_current"), style=dark),
        Slot(widget=w("ac", metric="lap_last"), style=dark),
        Slot(widget=w("ac", metric="lap_best"), style=st("#140a24", "#0a0512", "#a855f7")),
        Slot(widget=w("ac", metric="position"), style=dark),
        Slot(label=_t(lang, "headlights"), icon="lightbulb", action=ac("ACTION_HEADLIGHTS"),
             style=st("#3a2a05", "#1a1302", "#facc15")),
        Slot(label=_t(lang, "flash"), icon="zap", action=ac("ACTION_HEADLIGHTS_FLASH"),
             style=st("#3a2a05", "#1a1302", "#facc15")),
        Slot(label=_t(lang, "horn"), icon="megaphone", action=ac("ACTION_HORN"), style=st("#1e2533", "#0d1118")),
        Slot(label=_t(lang, "hazards"), icon="triangle-alert", action=ac("__EXT_HAZARDS"),
             style=st("#431407", "#1c0802", "#f97316")),
        Slot(label=_t(lang, "wipers"), icon="cloud-rain", action=ac("__EXT_WIPERS_MORE"),
             long_action=ac("__EXT_WIPERS_OFF"), style=st("#0c2d48", "#06182a", "#38bdf8")),
    ]
    p.zones = [
        Slot(widget=w("ac", metric="brake_bias"), style=st("#1f0a0a", "#0f0505", "#ef4444")),
        Slot(label=_t(lang, "tc"), icon="gauge", action=ac("TRACTION_CONTROL"), style=st("#0a1f14", "#050f0a", "#22c55e")),
        Slot(widget=w("ac", metric="fuel"), style=st("#1a1405", "#0d0a02", "#f59e0b")),
        Slot(widget=w("volume"), style=st("#14213d", "#0a1020")),
    ]
    p.knobs = [
        Knob(left=ac("BALANCEDN"), right=ac("BALANCEUP")),
        Knob(left=ac("TCDN"), right=ac("TCUP"), press=ac("TRACTION_CONTROL")),
        Knob(left=ac("__EXT_ENGINEMAP_DOWN"), right=ac("__EXT_ENGINEMAP_UP"), press=ac("__EXT_HIDE_UI")),
        Knob(left={"type": "volume", "op": "down", "step": 2}, right={"type": "volume", "op": "up", "step": 2},
             press={"type": "volume", "op": "mute"}),
    ]
    return p


def msfs(lang: str) -> Page:
    p = Page(id="msfs", name=_t(lang, "msfs"), icon="plane", accent="#22d3ee")
    ap = st("#062a33", "#03161b", "#22d3ee")
    sys_ = st("#1e2533", "#0d1118", "#22c55e")

    def toggle(label, event, var, style=ap, icon=None, on=None, off=None):
        cfg = {"var": var}
        if on:
            cfg["on_text"] = on
        if off:
            cfg["off_text"] = off
        return Slot(label=label, icon=icon, action=ev(event), widget=w("sim_toggle", **cfg), style=style)

    p.keys = [
        toggle("AP", "AP_MASTER", "AUTOPILOT MASTER", icon="plane"),
        toggle("NAV", "AP_NAV1_HOLD", "AUTOPILOT NAV1 LOCK", icon="navigation"),
        toggle("APR", "AP_APR_HOLD", "AUTOPILOT APPROACH HOLD", icon="plane-landing"),
        toggle("FLC", "FLIGHT_LEVEL_CHANGE", "AUTOPILOT FLIGHT LEVEL CHANGE", icon="trending-up"),
        toggle("YD", "YAW_DAMPER_TOGGLE", "AUTOPILOT YAW DAMPER", icon="move-horizontal"),
        toggle(_t(lang, "gear"), "GEAR_TOGGLE", "GEAR HANDLE POSITION", sys_, "circle-dot", on="GEAR ▼", off="GEAR ▲"),
        Slot(label=_t(lang, "flaps_up"), icon="chevrons-up", action=ev("FLAPS_DECR"), style=sys_),
        Slot(label=_t(lang, "flaps_dn"), action=ev("FLAPS_INCR"), style=sys_,
             widget=w("sim_value", var="FLAPS HANDLE INDEX", units="number", format="FLAPS {:.0f}")),
        toggle(_t(lang, "park"), "PARKING_BRAKES", "BRAKE PARKING POSITION", st("#3a0d14", "#1a0609", "#f43f5e"),
               "octagon-pause"),
        Slot(label=_t(lang, "landing"), icon="lightbulb", action=ev("LANDING_LIGHTS_TOGGLE"),
             long_action=ev("STROBES_TOGGLE"), widget=w("sim_toggle", var="LIGHT LANDING"),
             style=st("#3a2a05", "#1a1302", "#facc15")),
    ]
    p.zones = [
        Slot(action=ev("AP_PANEL_HEADING_HOLD"), style=ap, widget=w(
            "sim_value", var="AUTOPILOT HEADING LOCK DIR", units="degrees", format="HDG {:03.0f}°",
            active_var="AUTOPILOT HEADING LOCK")),
        Slot(action=ev("AP_PANEL_ALTITUDE_HOLD"), style=ap, widget=w(
            "sim_value", var="AUTOPILOT ALTITUDE LOCK VAR", units="feet", format="ALT {:,.0f}",
            active_var="AUTOPILOT ALTITUDE LOCK")),
        Slot(action=ev("AP_PANEL_VS_HOLD"), style=ap, widget=w(
            "sim_value", var="AUTOPILOT VERTICAL HOLD VAR", units="feet per minute", format="VS {:+,.0f}",
            active_var="AUTOPILOT VERTICAL HOLD")),
        Slot(action=ev("BAROMETRIC"), style=st("#1e2533", "#0d1118", "#94a3b8"), widget=w(
            "sim_value", var="KOHLSMAN SETTING HG", units="inHg", format="{:.2f} inHg")),
    ]
    p.knobs = [
        Knob(left=ev("HEADING_BUG_DEC"), right=ev("HEADING_BUG_INC"), press=ev("AP_PANEL_HEADING_HOLD")),
        Knob(left=ev("AP_ALT_VAR_DEC"), right=ev("AP_ALT_VAR_INC"), press=ev("AP_PANEL_ALTITUDE_HOLD")),
        Knob(left=ev("AP_VS_VAR_DEC"), right=ev("AP_VS_VAR_INC"), press=ev("AP_PANEL_VS_HOLD")),
        Knob(left=ev("KOHLSMAN_DEC"), right=ev("KOHLSMAN_INC"), press=ev("BAROMETRIC")),
    ]
    return p


def msfs_flight(lang: str) -> Page:
    p = Page(id="msfs2", name=_t(lang, "msfs2"), icon="plane-takeoff", accent="#22d3ee")
    inst = st("#020617", "#0b1222", "#22d3ee")
    lights = st("#3a2a05", "#1a1302", "#facc15")

    def val(var, units, fmt, label):
        return Slot(label=label, widget=w("sim_value", var=var, units=units, format=fmt), style=inst)

    p.keys = [
        val("AIRSPEED INDICATED", "knots", "{:.0f}", "IAS kt"),
        val("INDICATED ALTITUDE", "feet", "{:,.0f}", "ALT ft"),
        val("VERTICAL SPEED", "feet per minute", "{:+,.0f}", "VS fpm"),
        val("PLANE HEADING DEGREES MAGNETIC", "degrees", "{:03.0f}°", "HDG"),
        Slot(label=_t(lang, "on_ground"), icon="mountain", widget=w("sim_toggle", var="SIM ON GROUND"),
             style=st("#14261a", "#08120c", "#22c55e")),
        Slot(label=_t(lang, "pause"), icon="pause", action=ev("PAUSE_TOGGLE"), style=st("#1e2533", "#0d1118")),
        Slot(label=_t(lang, "autostart"), icon="power", action=ev("ENGINE_AUTO_START"),
             long_action=ev("ENGINE_AUTO_SHUTDOWN"), style=st("#3a0d14", "#1a0609", "#f43f5e")),
        Slot(label=_t(lang, "beacon"), icon="siren", action=ev("TOGGLE_BEACON_LIGHTS"),
             widget=w("sim_toggle", var="LIGHT BEACON"), style=lights),
        Slot(label=_t(lang, "navlt"), icon="sparkles", action=ev("TOGGLE_NAV_LIGHTS"),
             widget=w("sim_toggle", var="LIGHT NAV"), style=lights),
        Slot(label=_t(lang, "strobe"), icon="zap", action=ev("STROBES_TOGGLE"),
             widget=w("sim_toggle", var="LIGHT STROBE"), style=lights),
    ]
    p.zones = [
        Slot(label="COM1", widget=w("sim_value", var="COM ACTIVE FREQUENCY:1", units="MHz", format="{:.3f}"),
             style=inst),
        Slot(label=_t(lang, "taxi"), icon="lamp", action=ev("TOGGLE_TAXI_LIGHTS"),
             widget=w("sim_toggle", var="LIGHT TAXI"), style=lights),
        Slot(label="Spoilers", widget=w("sim_value", var="SPOILERS HANDLE POSITION", units="percent",
                                        format="{:.0f}%"), action=ev("SPOILERS_ARM_TOGGLE"), style=inst),
        Slot(widget=w("page_name"), style=st("#1e1b4b", "#0b0a1f", "#818cf8"), action={"type": "page", "target": "menu"}),
    ]
    p.knobs = [
        Knob(left=ev("COM_RADIO_WHOLE_DEC"), right=ev("COM_RADIO_WHOLE_INC"), press=ev("COM_STBY_RADIO_SWAP")),
        Knob(left=ev("COM_RADIO_FRACT_DEC"), right=ev("COM_RADIO_FRACT_INC"), press=ev("COM_STBY_RADIO_SWAP")),
        Knob(left=ev("SPOILERS_OFF"), right=ev("SPOILERS_ON"), press=ev("SPOILERS_ARM_TOGGLE")),
        Knob(left={"type": "page", "target": "prev"}, right={"type": "page", "target": "next"},
             press={"type": "page", "target": "msfs"}),
    ]
    return p


def build_default_profile(lang: str) -> Profile:
    return Profile(
        name="FreeDock", brightness=70, start_page="daily",
        pages=[daily(lang), windows(lang), assetto(lang), msfs(lang), msfs_flight(lang),
               *(build(lang) for build in NEW_PAGES.values())],
        auto_switch=[{"process": "acs.exe", "page": "assetto"},
                     {"process": "FlightSimulator2024.exe", "page": "msfs"},
                     {"process": "FlightSimulator.exe", "page": "msfs"}, *NEW_RULES],
        idle_minutes=15, idle_mode="clock",
        seen_presets=["daily", "windows", "assetto", "msfs", "msfs2", *NEW_PAGES, "assetto+rgb", "daily+audio_out"])


# --------------------------------------------------------------------------- upgrades
NEW_PAGES: dict = {}     # page id -> builder(lang), pages added after 0.1


def add_new_presets(profile: Profile, lang: str) -> bool:
    """Offer built-in pages added in newer versions to an existing profile, once each."""
    changed = False
    for pid, build in NEW_PAGES.items():
        if pid in profile.seen_presets:
            continue
        profile.seen_presets.append(pid)
        changed = True
        if profile.page(pid) is None:
            profile.pages.append(build(lang))
            known = {r.get("process", "").lower() for r in profile.auto_switch}
            profile.auto_switch += [r for r in NEW_RULES if r["page"] == pid and r["process"].lower() not in known]
    # 0.4: the Daily page's deck-brightness zone + knob became the audio output switch. Only slots still
    # as shipped are replaced; anything the user edited stays.
    if "daily+audio_out" not in profile.seen_presets:
        profile.seen_presets.append("daily+audio_out")
        changed = True
        page = profile.page("daily")
        if page and len(page.zones) > 1 and _is_stock_brightness_zone(page.zones[1]):
            page.zones[1] = audio_output_zone()
        if page and len(page.knobs) > 1 and (page.knobs[1].left or {}).get("type") == "brightness"                 and (page.knobs[1].right or {}).get("type") == "brightness":
            page.knobs[1] = audio_output_knob()
    # 0.2: the built-in Assetto page also drives RGB shift lights
    if "assetto+rgb" not in profile.seen_presets:
        profile.seen_presets.append("assetto+rgb")
        changed = True
        page = profile.page("assetto")
        if page and "ac_shift_lights" in page.effects and "rgb_shift_lights" not in page.effects:
            page.effects.append("rgb_shift_lights")
    return changed


def _is_stock_brightness_zone(slot: Slot) -> bool:
    return (slot.widget is None and slot.icon == "sun" and (slot.action or {}).get("type") == "deck"
            and slot.label in {v for v in L["deck"].values()})


# --------------------------------------------------------------------------- pages added in 0.2
L.update({
    "ets2": {"en": "Euro Truck", "pt": "Euro Truck", "es": "Euro Truck"},
    "beamng": {"en": "BeamNG.drive", "pt": "BeamNG.drive", "es": "BeamNG.drive"},
    "ai": {"en": "AI assistants", "pt": "Assistentes de IA", "es": "Asistentes de IA"},
    "sounds": {"en": "Sounds & DJ", "pt": "Sons & DJ", "es": "Sonidos y DJ"},
    "stop_all": {"en": "Stop all", "pt": "Parar tudo", "es": "Parar todo"},
    "hear_too": {"en": "Hear it too", "pt": "Ouvir junto", "es": "Oir tambien"},
    "engine": {"en": "Engine", "pt": "Motor", "es": "Motor"},
    "parking": {"en": "Park brake", "pt": "Freio mão", "es": "Freno mano"},
    "camera": {"en": "Camera", "pt": "Câmera", "es": "Cámara"},
    "ignition": {"en": "Ignition", "pt": "Ignição", "es": "Encendido"},
    "recover": {"en": "Recover", "pt": "Recuperar", "es": "Recuperar"},
    "slowmo": {"en": "Slow-mo", "pt": "Câmera lenta", "es": "Cámara lenta"},
    "esc": {"en": "ESC / TC", "pt": "ESC / TC", "es": "ESC / TC"},
    "clear": {"en": "Clear alerts", "pt": "Limpar avisos", "es": "Borrar avisos"},
    "focus": {"en": "Focus", "pt": "Foco", "es": "Foco"},
    "ets2_trip": {"en": "Truck trip", "pt": "Viagem", "es": "Viaje"},
    "wipers": {"en": "Wipers", "pt": "Limpador", "es": "Limpiaparabrisas"},
    "high_beam": {"en": "High beam", "pt": "Farol alto", "es": "Luz larga"},
    "trailer": {"en": "Trailer", "pt": "Carreta", "es": "Remolque"},
    "engine_brake": {"en": "Engine brake", "pt": "Freio motor", "es": "Freno motor"},
})


def e2(binding: str) -> dict:
    return {"type": "ets2_binding", "binding": binding}


def bn(binding: str) -> dict:
    return {"type": "beamng_binding", "binding": binding}


def _nav(home: str) -> Knob:
    return Knob(left={"type": "page", "target": "prev"}, right={"type": "page", "target": "next"},
                press={"type": "page", "target": home})


PAGE_NAME_ZONE_STYLE = st("#1e1b4b", "#0b0a1f", "#818cf8")


def ets2(lang: str) -> Page:
    """Cab page: what you touch while driving. Blinkers flash in sync with the dashboard lamps."""
    p = Page(id="ets2", name=_t(lang, "ets2"), icon="truck", accent="#f59e0b", effects=["rgb_shift_lights"],
             style={"change": "slide"})
    dash = st("#0b0f19", "#151b2b", "#f59e0b")
    warm = st("#3a2a05", "#1a1302", "#facc15")
    p.keys = [
        Slot(label=_t(lang, "wipers"), icon="droplets", action=e2("wipers"), long_action=e2("wipersback"),
             widget=w("ets2", metric="wipers"), style=st("#0c1f2e", "#06101a", "#38bdf8")),
        Slot(widget=w("ets2", metric="speed"), action=e2("cruiectrl"), style=dash),
        Slot(widget=w("ets2", metric="gear"), style=st("#000000", "#111111", "#22c55e")),
        Slot(widget=w("ets2", metric="fuel"), style=dash),
        Slot(widget=w("ets2", metric="fatigue"), action=e2("navmap"), style=dash),
        Slot(label=_t(lang, "engine"), icon="power", action=e2("engine"), long_action=e2("engineelect"),
             widget=w("ets2", metric="engine_on"), style=st("#14261a", "#08120c", "#22c55e")),
        Slot(label=_t(lang, "parking"), icon="circle-parking", action=e2("parkingbrake"),
             widget=w("ets2", metric="parking_brake"), style=st("#3a0d14", "#1a0609", "#ef4444")),
        Slot(label=_t(lang, "headlights"), icon="lightbulb", action=e2("light"), long_action=e2("flasher4way"),
             widget=w("ets2", metric="lights_low"), style=warm),
        Slot(label=_t(lang, "high_beam"), icon="sun", action=e2("hblight"), long_action=e2("lighthorn"),
             widget=w("ets2", metric="lights_high"), style=st("#0c1a3a", "#060c1c", "#3b82f6")),
        Slot(label=_t(lang, "trailer"), icon="link", action=e2("attach"),
             widget=w("ets2", metric="trailer_attached"), style=st("#14261a", "#08120c", "#22c55e")),
    ]
    p.zones = [
        Slot(widget=w("ets2", metric="cruise"), action=e2("cruiectrl"), style=dash),
        Slot(label=_t(lang, "engine_brake"), icon="gauge", widget=w("ets2", metric="engine_brake"),
             action=e2("engbraketog"), style=dash),
        Slot(widget=w("ets2", metric="trip"), action=e2("navmap"), style=dash),
        Slot(widget=w("page_name"), style=PAGE_NAME_ZONE_STYLE, action={"type": "page", "target": "menu"}),
    ]
    p.knobs = _ets2_knobs()
    return p


def _ets2_knobs() -> list[Knob]:
    return [
        Knob(left=e2("cruiectrldec"), right=e2("cruiectrlinc"), press=e2("cruiectrlres")),
        Knob(left=e2("retarderdown"), right=e2("retarderup"), press=e2("engbraketog")),
        Knob(left=e2("lookleft"), right=e2("lookright"), press=e2("camcycle")),
        _nav("ets2"),
    ]


def ets2_trip(lang: str) -> Page:
    """Trip page: the job, the route advisor and the driver - the SCS SDK has no turn-by-turn data."""
    p = Page(id="ets2_trip", name=_t(lang, "ets2_trip"), icon="map", accent="#38bdf8", effects=["rgb_shift_lights"],
             style={"change": "slide"})
    dash = st("#0b0f19", "#151b2b", "#38bdf8")
    p.keys = [
        Slot(widget=w("ets2", metric="job"), action=e2("navmap"), style=dash),
        Slot(widget=w("ets2", metric="deadline"), style=dash),
        Slot(widget=w("ets2", metric="trip"), action=e2("advmouse"), style=dash),
        Slot(widget=w("ets2", metric="compass"), style=dash),
        Slot(widget=w("ets2", metric="clock"), style=dash),
        Slot(widget=w("ets2", metric="adblue"), style=dash),
        Slot(widget=w("ets2", metric="damage"), style=dash),
        Slot(widget=w("ets2", metric="range"), style=dash),
        Slot(label=_t(lang, "hazards"), icon="triangle-alert", action=e2("flasher4way"), long_action=e2("airhorn"),
             widget=w("ets2", metric="hazard"), style=st("#3a1d05", "#1a0d02", "#f97316")),
        Slot(label=_t(lang, "beacon"), icon="siren", action=e2("beacon"), widget=w("ets2", metric="beacon"),
             style=st("#3a2a05", "#1a1302", "#facc15")),
    ]
    p.zones = [
        Slot(widget=w("ets2", metric="limit"), style=dash),
        Slot(widget=w("ets2", metric="retarder"), action=e2("engbraketog"), style=dash),
        Slot(label=_t(lang, "camera"), icon="camera", action=e2("camcycle"), long_action=e2("horn"), style=SLATE),
        Slot(widget=w("page_name"), style=PAGE_NAME_ZONE_STYLE, action={"type": "page", "target": "menu"}),
    ]
    p.knobs = _ets2_knobs()
    return p


def beamng(lang: str) -> Page:
    p = Page(id="beamng", name=_t(lang, "beamng"), icon="car-front", accent="#f97316", effects=["rgb_shift_lights"],
             style={"change": "zoom"})
    dash = st("#0b0f19", "#151b2b", "#f97316")
    warm = st("#3a2a05", "#1a1302", "#facc15")
    p.keys = [
        Slot(widget=w("beamng", metric="speed"), style=dash),
        Slot(widget=w("beamng", metric="gear"), style=st("#000000", "#111111", "#22c55e")),
        Slot(widget=w("beamng", metric="fuel"), style=dash),
        Slot(widget=w("beamng", metric="eng_temp"), style=dash),
        Slot(widget=w("beamng", metric="turbo"), style=dash),
        Slot(label=_t(lang, "ignition"), icon="power", action=bn("activateStarterMotor"),
             long_action=bn("setIgnitionLevel0"), widget=w("beamng", metric="engine"),
             style=st("#14261a", "#08120c", "#22c55e")),
        Slot(label=_t(lang, "parking"), icon="circle-parking", action=bn("parkingbrake_toggle"),
             widget=w("beamng", metric="handbrake"), style=st("#3a0d14", "#1a0609", "#ef4444")),
        Slot(label=_t(lang, "headlights"), icon="lightbulb", action=bn("toggle_headlights"), long_action=bn("highbeam"),
             widget=w("beamng", metric="full_beam"), style=warm),
        Slot(label=_t(lang, "hazards"), icon="triangle-alert", action=bn("toggle_hazard_signal"),
             widget=w("beamng", metric="hazard"), style=st("#3a1d05", "#1a0d02", "#f97316")),
        Slot(label=_t(lang, "recover"), icon="rotate-ccw", action=bn("recover_vehicle"), long_action=bn("reset_physics"),
             style=st("#0c2d48", "#06182a", "#38bdf8")),
    ]
    p.zones = [
        Slot(label=_t(lang, "esc"), icon="gauge", action=bn("toggleESCMode"), widget=w("beamng", metric="tc"),
             style=dash),
        Slot(label=_t(lang, "camera"), icon="camera", action=bn("switch_camera_next"), long_action=bn("toggleCamera"),
             style=SLATE),
        Slot(label=_t(lang, "slowmo"), icon="snail", action=bn("toggle_slow_motion"), style=SLATE),
        Slot(widget=w("page_name"), style=PAGE_NAME_ZONE_STYLE, action={"type": "page", "target": "menu"}),
    ]
    p.knobs = [
        Knob(left=bn("toggle_left_signal"), right=bn("toggle_right_signal"), press=bn("horn")),
        Knob(left=bn("switch_camera_prev"), right=bn("switch_camera_next"), press=bn("toggleCamera")),
        Knob(left=bn("slower_motion"), right=bn("faster_motion"), press=bn("pause")),
        _nav("beamng"),
    ]
    return p


def ai_page(lang: str) -> Page:
    p = Page(id="ai", name=_t(lang, "ai"), icon="sparkles", accent="#d97757")
    claude = st("#2a1408", "#140a04", "#d97757")
    codex = st("#06231b", "#03120d", "#10a37f")
    opencode = st("#2b2105", "#151002", "#f5a623")

    def status(src, style, tool=None):
        return Slot(widget=w("ai", source=src, show="status"), style=style,
                    action={"type": "ai", "tool": tool or src, "op": "open_ack"})

    def usage(src, show, style, label):
        return Slot(label=label, widget=w("ai", source=src, show=show), style=style)

    p.keys = [
        status("claude", claude),
        status("codex", codex, "codex_app"),
        status("opencode", opencode),
        usage("claude", "usage_5h", claude, "Claude 5h"),
        usage("claude", "usage_week", claude, "Claude 7d"),
        Slot(label="Claude Code", icon="square-terminal", action={"type": "ai", "tool": "claude", "op": "open"},
             style=claude),
        Slot(label="Codex", icon="app-window", action={"type": "ai", "tool": "codex_app", "op": "open"},
             long_action={"type": "ai", "tool": "codex", "op": "open"}, style=codex),
        Slot(label="OpenCode", icon="code-xml", action={"type": "ai", "tool": "opencode", "op": "open"},
             style=opencode),
        usage("codex", "usage_5h", codex, "Codex 5h"),
        Slot(label=_t(lang, "clear"), icon="bell-off", action={"type": "ai", "tool": "all", "op": "ack"},
             style=SLATE),
    ]
    p.zones = [
        usage("claude", "tokens_today", claude, "Claude"),
        Slot(label=_t(lang, "focus"), widget=w("pomodoro", minutes=50), long_action={"type": "timer", "op": "reset"},
             style=st("#2a1240", "#12081c", "#c084fc")),
        Slot(widget=w("clock", format="24h", date=True), style=st("#111827", "#1f2937")),
        Slot(widget=w("page_name"), style=PAGE_NAME_ZONE_STYLE, action={"type": "page", "target": "menu"}),
    ]
    p.knobs = [
        Knob(left={"type": "volume", "op": "down", "step": 2}, right={"type": "volume", "op": "up", "step": 2},
             press={"type": "volume", "op": "mute"}),
        Knob(left={"type": "scroll", "steps": 3}, right={"type": "scroll", "steps": -3},
             press={"type": "ai", "tool": "all", "op": "ack"}),
        Knob(left={"type": "media", "op": "prevtrack"}, right={"type": "media", "op": "nexttrack"},
             press={"type": "media", "op": "playpause"}),
        _nav("ai"),
    ]
    return p


def sounds_page(lang: str) -> Page:
    """Soundboard: tap = into the microphone (friends hear it), hold = headset only."""
    from .soundboard.synth import BUILTINS
    p = Page(id="sounds", name=_t(lang, "sounds"), icon="audio-lines", accent="#22c55e")
    keys = []
    for sid, (_names, _icon, color, _build) in BUILTINS.items():
        bg = QColor(color).darker(520).name()
        keys.append(Slot(action={"type": "sound", "sound": sid, "target": "mic", "hold": "local"},
                         style=st(bg, "#07090d", color)))
    keys.append(Slot(label=_t(lang, "stop_all"), icon="circle-stop", action={"type": "soundboard", "op": "stop"},
                     style=st("#3b0d0d", "#140505", "#ef4444")))
    keys.append(Slot(label=_t(lang, "hear_too"), icon="headphones", action={"type": "soundboard", "op": "monitor"},
                     style=st("#0c2340", "#060f1c", "#3b82f6")))
    p.keys = keys
    p.zones = [
        Slot(widget=w("volume"), style=st("#14213d", "#0a1020")),
        Slot(widget=w("mic_level"), style=st("#0f2a1d", "#07130d", "#22c55e")),
        Slot(widget=w("soundboard"), style=st("#2a1240", "#12081c", "#c084fc")),
        Slot(widget=w("page_name"), style=PAGE_NAME_ZONE_STYLE, action={"type": "page", "target": "menu"}),
    ]
    p.knobs = [
        Knob(left={"type": "volume", "op": "down", "step": 2}, right={"type": "volume", "op": "up", "step": 2},
             press={"type": "volume", "op": "mute"}),
        Knob(left={"type": "mic", "op": "down", "step": 2}, right={"type": "mic", "op": "up", "step": 2},
             press={"type": "mic", "op": "toggle"}),
        Knob(left={"type": "soundboard", "op": "down", "step": 5}, right={"type": "soundboard", "op": "up", "step": 5},
             press={"type": "soundboard", "op": "stop"}),
        Knob(left={"type": "media", "op": "prevtrack"}, right={"type": "media", "op": "nexttrack"},
             press={"type": "media", "op": "playpause"}),
    ]
    return p


NEW_PAGES.update({"ai": ai_page, "ets2": ets2, "ets2_trip": ets2_trip, "beamng": beamng, "sounds": sounds_page})
NEW_RULES = [{"process": "eurotrucks2.exe", "page": "ets2"}, {"process": "amtrucks.exe", "page": "ets2"},
             {"process": "BeamNG.drive.x64.exe", "page": "beamng"}]
