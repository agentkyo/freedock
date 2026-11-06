"""Live widgets: functions that turn current data into a :class:`Visual` every frame.

They are called ~30 times per second, so they must only read cached data (background threads
fill it). The engine re-renders a key only when the returned Visual actually changes.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from .actions import Field
from .i18n import tr
from .render import Visual

if TYPE_CHECKING:
    from .engine import Engine, SlotRef


@dataclass(frozen=True)
class WidgetType:
    id: str
    category: str
    icon: str
    fields: tuple[Field, ...] = ()
    fn: Callable[..., Visual] = field(default=lambda e, s, c, v: v)
    press: dict | None = None        # default action when the slot has none (e.g. timers)
    needs: tuple[str, ...] = ()      # integrations to start: stats, assetto, msfs, obs, weather


WIDGETS: dict[str, WidgetType] = {}


def widget(id: str, category: str, icon: str, *fields: Field, press: dict | None = None,
           needs: tuple[str, ...] = ()):
    def wrap(fn):
        WIDGETS[id] = WidgetType(id, category, icon, tuple(fields), fn, press, needs)
        return fn
    return wrap


def _load_color(frac: float) -> str:
    if frac < 0.6:
        return "#22c55e"
    if frac < 0.85:
        return "#f59e0b"
    return "#ef4444"


def _days(lang: str) -> list[str]:
    return {"pt": ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"],
            "es": ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]}.get(
        lang, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"])


def _months(lang: str) -> list[str]:
    return {"pt": "jan fev mar abr mai jun jul ago set out nov dez".split(),
            "es": "ene feb mar abr may jun jul ago sep oct nov dic".split()}.get(
        lang, "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split())


# --------------------------------------------------------------------------- time
@widget("clock", "time", "clock", Field("format", "choice", "24h", (("24h", "24h"), ("12h", "12h"))),
        Field("seconds", "bool", False), Field("date", "bool", True))
def _clock(e: "Engine", s: "SlotRef", c: dict, v: Visual) -> Visual:
    now = datetime.now()
    fmt = "%I:%M" if c.get("format") == "12h" else "%H:%M"
    if c.get("seconds"):
        fmt += ":%S"
    text = now.strftime(fmt).lstrip("0") if c.get("format") == "12h" else now.strftime(fmt)
    sub = None
    if c.get("date", True):
        sub = f"{_days(e.lang)[now.weekday()]}, {now.day:02d} {_months(e.lang)[now.month - 1]}"
    return v.with_(value=text, sub=sub, mono=True, label="")


@widget("date", "time", "calendar")
def _date(e, s, c, v):
    now = datetime.now()
    return v.with_(value=str(now.day), sub=f"{_days(e.lang)[now.weekday()]} · {_months(e.lang)[now.month - 1]}",
                   label="")


@widget("pomodoro", "time", "timer", Field("minutes", "int", 25), press={"type": "timer", "op": "toggle"})
def _pomodoro(e, s, c, v):
    total = max(1, int(c.get("minutes", 25))) * 60
    t = e.timer(s, total)
    left = t.remaining()
    done = left <= 0 and t.started
    mm, ss = divmod(int(max(0, left) + 0.999), 60)
    blink = done and int(time.time() * 2) % 2 == 0
    return v.with_(value=f"{mm:02d}:{ss:02d}", mono=True, progress=1 - left / total if t.started else 0.0,
                   progress_style="bar", active=t.running or blink, anim="alert" if done else None,
                   sub=tr("widget.pomodoro.done") if done else (v.label or "Pomodoro"), label="")


@widget("stopwatch", "time", "timer-reset", press={"type": "timer", "op": "toggle"})
def _stopwatch(e, s, c, v):
    t = e.timer(s, 0)
    el = t.elapsed()
    mm, ss = divmod(int(el), 60)
    return v.with_(value=f"{mm:02d}:{ss:02d}", mono=True, active=t.running, sub=v.label or None, label="")


# --------------------------------------------------------------------------- system
def _pct_widget(e, s, v, key, label, sub=None):
    val = e.stats.get(key)
    if val is None:
        return v.with_(value="--", label=label, progress=0)
    frac = e.smooth(s, "p", val / 100)
    return v.with_(value=f"{val:.0f}%", label=label, sub=sub, progress=frac, accent=_load_color(frac))


@widget("cpu", "system", "cpu", needs=("stats",))
def _cpu(e, s, c, v):
    return _pct_widget(e, s, v.with_(icon=v.icon or "cpu"), "cpu", v.label or "CPU")


@widget("ram", "system", "memory-stick", needs=("stats",))
def _ram(e, s, c, v):
    return _pct_widget(e, s, v.with_(icon=v.icon or "memory-stick"), "ram", v.label or "RAM",
                       f"{e.stats.get('ram_used_gb', 0):.1f} GB")


@widget("gpu", "system", "monitor-cog", needs=("stats",))
def _gpu(e, s, c, v):
    t = e.stats.get("gpu_temp")
    return _pct_widget(e, s, v.with_(icon=v.icon or "monitor-cog"), "gpu", v.label or "GPU",
                       f"{t:.0f}°C" if t is not None else None)


def _rate(b: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024 or unit == "GB":
            return f"{b:.0f} {unit}/s" if unit in ("B", "KB") else f"{b:.1f} {unit}/s"
        b /= 1024
    return ""


@widget("network", "system", "activity", needs=("stats",))
def _net(e, s, c, v):
    return v.with_(value="↓ " + _rate(e.stats.get("net_down", 0)), sub="↑ " + _rate(e.stats.get("net_up", 0)),
                   icon=v.icon or "activity", label="")


@widget("weather", "system", "cloud-sun", needs=("weather",))
def _weather(e, s, c, v):
    w = e.weather_data()
    if not w:
        return v.with_(icon="cloud-off", label=tr("widget.weather.setup"))
    return v.with_(icon=w["icon"], value=f"{w['temp']:.0f}°", sub=e.settings.weather.get("city") or w["text"],
                   label="", anim="float")


@widget("volume", "media", "volume-2", press={"type": "volume", "op": "mute"})
def _volume(e, s, c, v):
    level, muted = e.audio.get("level"), e.audio.get("muted")
    if level is None:
        return v.with_(icon="volume-2", label=v.label or tr("action.volume"))
    frac = e.smooth(s, "vol", level)
    icon = "volume-x" if muted else "volume-1" if level < 0.34 else "volume-2"
    return v.with_(icon=icon, value=f"{level * 100:.0f}%", progress=frac, progress_style="bar",
                   label=v.label or tr("action.volume"), active=bool(muted),
                   accent="#ef4444" if muted else v.accent)


@widget("mic", "media", "mic", press={"type": "mic", "op": "toggle"})
def _mic(e, s, c, v):
    muted = e.audio.get("mic_muted")
    if muted is None:
        return v.with_(icon="mic", label=v.label or "Mic")
    return v.with_(icon="mic-off" if muted else "mic", active=bool(muted), accent="#ef4444" if muted else "#22c55e",
                   anim="pulse" if muted else None,
                   label=tr("widget.mic.muted") if muted else tr("widget.mic.live"))


@widget("page_name", "deck", "layers")
def _page_name(e, s, c, v):
    pages = e.visible_pages()
    idx = next((i for i, p in enumerate(pages) if p.id == e.page.id), 0)
    dots = "".join("●" if i == idx else "○" for i in range(len(pages)))
    return v.with_(value=e.page.name, sub=dots, icon=e.page.icon or v.icon, label="", anim="shimmer")


@widget("obs_state", "web", "video", Field("which", "choice", "record", (
        ("record", "obs.record"), ("stream", "obs.stream"))), needs=("obs",))
def _obs_state(e, s, c, v):
    from .integrations.obs import client
    cl = client()
    on = cl.state.get("recording" if c.get("which", "record") == "record" else "streaming")
    if not cl.connected:
        return v.with_(dim=0.45)
    return v.with_(active=bool(on), badge="REC" if on and c.get("which", "record") == "record" else
                   ("LIVE" if on else None), accent="#ef4444" if on else v.accent)


# --------------------------------------------------------------------------- Assetto Corsa
AC_METRICS = (("gear", "ac.gear"), ("speed", "ac.speed"), ("rpm", "ac.rpm"), ("lap_current", "ac.lap_current"),
              ("lap_last", "ac.lap_last"), ("lap_best", "ac.lap_best"), ("position", "ac.position"),
              ("fuel", "ac.fuel"), ("brake_bias", "ac.brake_bias"), ("laps", "ac.laps"),
              ("tyres", "ac.tyres"), ("pit_limiter", "ac.pit_limiter"))


@widget("ac", "games", "car", Field("metric", "choice", "gear", AC_METRICS), needs=("assetto",))
def _ac(e, s, c, v):
    from .integrations.assetto import fmt_lap
    d = e.assetto()
    m = c.get("metric", "gear")
    if not d.get("connected"):
        return v.with_(dim=0.5, value=None, icon=v.icon or "car", label=v.label or tr(f"ac.{m}"))
    if m == "gear":
        frac = d["rpm"] / max(1, d["max_rpm"])
        return v.with_(value=d["gear"], sub=f"{d['speed']:.0f} km/h", mono=True, label="",
                       progress=e.smooth(s, "rpm", frac, 0.5), progress_style="bar", accent=_load_color(frac))
    if m == "speed":
        return v.with_(value=f"{d['speed']:.0f}", sub="km/h", mono=True, label="")
    if m == "rpm":
        frac = d["rpm"] / max(1, d["max_rpm"])
        return v.with_(value=f"{d['rpm']}", sub="RPM", mono=True, progress=e.smooth(s, "rpm", frac, 0.5),
                       accent=_load_color(frac), label="")
    if m in ("lap_current", "lap_last", "lap_best"):
        ms = d[{"lap_current": "current_ms", "lap_last": "last_ms", "lap_best": "best_ms"}[m]]
        best = d["best_ms"]
        accent = v.accent
        if m == "lap_last" and best > 0 and ms == best:
            accent = "#a855f7"   # purple = personal best
        return v.with_(value=fmt_lap(ms), sub=v.label or tr(f"ac.{m}"), mono=True, label="", accent=accent,
                       active=accent != v.accent)
    if m == "position":
        return v.with_(value=f"P{d['position']}", sub=tr("ac.lap_n", n=d["laps"] + 1), mono=True, label="")
    if m == "fuel":
        frac = d["fuel"] / max(1.0, d["max_fuel"])
        return v.with_(value=f"{d['fuel']:.1f} L", progress=frac, progress_style="bar", mono=True,
                       sub=tr("ac.fuel"), label="", accent=_load_color(1 - frac))
    if m == "brake_bias":
        return v.with_(value=f"{d['brake_bias'] * 100:.1f}%", sub=tr("ac.brake_bias"), mono=True, label="")
    if m == "laps":
        total = d["total_laps"]
        return v.with_(value=f"{d['laps']}/{total}" if total else str(d["laps"]), sub=tr("ac.laps"), mono=True,
                       label="")
    if m == "tyres":
        t = d["tyre_core"]
        return v.with_(value=f"{t[0]:.0f} {t[1]:.0f}", sub=f"{t[2]:.0f} {t[3]:.0f} °C", mono=True, label="")
    if m == "pit_limiter":
        return v.with_(active=d["pit_limiter"], accent="#3b82f6", badge="ON" if d["pit_limiter"] else None)
    return v


# --------------------------------------------------------------------------- MSFS
@widget("sim_value", "games", "gauge", Field("var", "text", "INDICATED ALTITUDE"), Field("units", "text", "feet"),
        Field("format", "text", "{:,.0f}"), Field("active_var", "text", ""), needs=("msfs",))
def _sim_value(e, s, c, v):
    sim = e.sim()
    var = c.get("var", "")
    sim.watch(var, c.get("units") or "number")
    if c.get("active_var"):
        sim.watch(c["active_var"], "bool")
    if not sim.connected:
        return v.with_(dim=0.5, value="---", mono=True, sub=v.label or var.title()[:18], label="")
    val = sim.get(var)
    try:
        text = c.get("format", "{:,.0f}").format(val) if val is not None else "---"
    except (ValueError, TypeError):
        text = str(val)
    active = bool(c.get("active_var")) and (sim.get(c["active_var"], 0) or 0) > 0.5
    return v.with_(value=text, sub=v.label or None, mono=True, label="", active=active)


@widget("sim_toggle", "games", "toggle-right", Field("var", "text", "AUTOPILOT MASTER"),
        Field("on_text", "text", ""), Field("off_text", "text", ""), needs=("msfs",))
def _sim_toggle(e, s, c, v):
    sim = e.sim()
    var = c.get("var", "")
    sim.watch(var, "number")
    if not sim.connected:
        return v.with_(dim=0.55)
    on = (sim.get(var, 0) or 0) > 0.5
    text = c.get("on_text") if on else c.get("off_text")
    return v.with_(active=on, label=text or v.label)


# --------------------------------------------------------------------------- AI assistants
AI_SOURCES = (("claude", "ai.claude"), ("codex", "ai.codex"), ("opencode", "ai.opencode"), ("custom", "ai.custom"))
AI_SHOW = (("status", "ai.show.status"), ("usage_5h", "ai.show.usage_5h"), ("usage_week", "ai.show.usage_week"),
           ("tokens_today", "ai.show.tokens_today"))


def _tokens(n: float | None) -> str:
    if not n:
        return "0"
    for unit, div in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if n >= div:
            return f"{n / div:.1f}{unit}".replace(".0", "")
    return f"{n:.0f}"


def _until(epoch: float | None) -> str | None:
    if not epoch:
        return None
    left = int(epoch - time.time())
    if left <= 0:
        return None
    h, m = divmod(left // 60, 60)
    if h >= 24:
        return f"↻ {h // 24}d {h % 24}h"
    return f"↻ {h}h{m:02d}" if h else f"↻ {m} min"


@widget("ai", "ai", "sparkles", Field("source", "choice", "claude", AI_SOURCES),
        Field("show", "choice", "status", AI_SHOW), press={"type": "ai", "op": "open"}, needs=("ai",))
def _ai(e, s, c, v):
    from .notify import EVENT_COLOR, source as meta_of
    src = c.get("source", "claude")
    meta = meta_of(src)
    show = c.get("show", "status")
    icon = v.icon or meta.icon
    if show != "status":
        u = (e.ai_usage().get(src) or {})
        if not u.get("installed", True) and not u:
            return v.with_(dim=0.5, icon=icon, label=v.label or meta.name)
        if show == "tokens_today":
            return v.with_(icon=icon, value=_tokens(u.get("tokens_today")), sub=v.label or tr("ai.tokens_today"),
                           mono=True, label="")
        pct = u.get("percent_5h" if show == "usage_5h" else "percent_week")
        if pct is None:
            # no plan percentage for this tool: show tokens of the window instead
            tok = u.get("tokens_5h") if show == "usage_5h" else u.get("tokens_week", u.get("tokens_today"))
            return v.with_(icon=icon, value=_tokens(tok), mono=True, label="",
                           sub=v.label or tr("ai.tokens_5h" if show == "usage_5h" else "ai.tokens_today"))
        frac = e.smooth(s, "ai", max(0.0, min(1.0, pct / 100)))
        return v.with_(icon=icon, value=f"{pct:.0f}%", progress=frac, accent=_load_color(frac), mono=True,
                       label="", sub=_until(u.get("resets_at" if show == "usage_5h" else "resets_week_at"))
                       or v.label or tr("ai.show." + show))
    st = e.notify_center.get(src)
    name = v.label or meta.name
    if st.status == "working":
        return v.with_(icon="loader-circle", value=None, label=tr("ai.working"), anim="spin", accent=meta.color,
                       progress=st.progress, progress_style="bar", badge=name[:8] if v.label else None)
    if st.status in ("done", "question", "error"):
        color = EVENT_COLOR.get(st.status, meta.color)
        ico = {"done": "circle-check-big", "question": "message-circle-question", "error": "circle-x"}[st.status]
        return v.with_(icon=ico, label=tr(f"ai.{st.status}"), accent=color, active=st.unseen,
                       anim="alert" if st.unseen else None, badge="!" if st.unseen else None)
    return v.with_(icon=icon, label=name, accent=v.accent if v.accent != e.page.accent else meta.color)


# --------------------------------------------------------------------------- Euro Truck / American Truck Simulator
ETS2_METRICS = tuple((m, f"ets2.{m}") for m in (
    "speed", "gear", "rpm", "cruise", "fuel", "range", "damage", "job", "nav", "rest", "clock", "limit",
    "parking_brake", "lights_low", "lights_high", "beacon", "hazard", "engine_on", "diff_lock", "lift_axle",
    "retarder", "trailer_attached", "wipers", "blinker_left", "blinker_right", "adblue"))
ETS2_STATES = {"parking_brake": "#ef4444", "lights_low": "#22c55e", "lights_high": "#3b82f6", "beacon": "#f59e0b",
               "hazard": "#f97316", "engine_on": "#22c55e", "diff_lock": "#a855f7", "lift_axle": "#38bdf8",
               "trailer_attached": "#22c55e", "wipers": "#38bdf8", "blinker_left": "#22c55e",
               "blinker_right": "#22c55e", "cruise_on": "#22c55e"}


def _fmt_min(minutes: float | None) -> str:
    if minutes is None:
        return "--"
    h, m = divmod(int(minutes), 60)
    return f"{h}h{m:02d}" if h else f"{m} min"


@widget("ets2", "games", "truck", Field("metric", "choice", "speed", ETS2_METRICS), needs=("ets2",))
def _ets2(e, s, c, v):
    d = e.ets2()
    m = c.get("metric", "speed")
    if not d.get("connected"):
        return v.with_(dim=0.5, value=None, icon=v.icon or "truck", label=v.label or tr(f"ets2.{m}"))
    if m in ETS2_STATES:
        key = "blinker_left_lamp" if m == "blinker_left" else "blinker_right_lamp" if m == "blinker_right" else m
        on = bool(d.get(key))
        return v.with_(active=on, accent=ETS2_STATES[m] if on else v.accent,
                       anim="blink" if on and m in ("hazard", "beacon") else None)
    if m == "speed":
        lim = d.get("speed_limit_kmh")
        over = lim and d.get("speed_kmh", 0) > lim + 3
        return v.with_(value=f"{d.get('speed_kmh', 0):.0f}", sub=f"km/h · {lim:.0f}" if lim else "km/h", mono=True,
                       label="", accent="#ef4444" if over else v.accent, active=bool(over),
                       anim="pulse" if over else None)
    if m == "limit":
        lim = d.get("speed_limit_kmh")
        return v.with_(value=f"{lim:.0f}" if lim else "--", sub=tr("ets2.limit"), mono=True, label="",
                       icon=v.icon or "octagon-alert")
    if m == "gear":
        frac = (d.get("rpm") or 0) / max(1, d.get("max_rpm") or 2500)
        return v.with_(value=str(d.get("gear", "N")), sub=f"{d.get('speed_kmh', 0):.0f} km/h", mono=True, label="",
                       progress=e.smooth(s, "rpm", frac, 0.5), progress_style="bar", accent=_load_color(frac))
    if m == "rpm":
        frac = (d.get("rpm") or 0) / max(1, d.get("max_rpm") or 2500)
        return v.with_(value=f"{d.get('rpm', 0):.0f}", sub="RPM", mono=True, label="",
                       progress=e.smooth(s, "rpm", frac, 0.5), accent=_load_color(frac))
    if m == "cruise":
        on = bool(d.get("cruise_on"))
        return v.with_(value=f"{d.get('cruise_kmh') or 0:.0f}" if on else "OFF", sub=tr("ets2.cruise"), mono=True,
                       label="", active=on, accent="#22c55e" if on else v.accent)
    if m == "fuel":
        cap = d.get("fuel_cap_l") or 1
        frac = (d.get("fuel_l") or 0) / cap
        return v.with_(value=f"{d.get('fuel_l', 0):.0f} L", sub=tr("ets2.fuel"), mono=True, label="",
                       progress=frac, progress_style="bar", accent=_load_color(1 - frac),
                       anim="pulse" if d.get("fuel_warning") else None)
    if m == "range":
        return v.with_(value=f"{d.get('fuel_range_km') or 0:.0f}", sub=tr("ets2.range") + " km", mono=True, label="")
    if m == "adblue":
        return v.with_(value=f"{d.get('adblue') or 0:.0f} L", sub="AdBlue", mono=True, label="",
                       anim="pulse" if d.get("adblue_warning") else None)
    if m == "damage":
        frac = max(d.get("truck_damage") or 0, 0)
        return v.with_(value=f"{frac * 100:.0f}%", sub=tr("ets2.damage"), mono=True, label="", progress=frac,
                       accent=_load_color(frac))
    if m == "job":
        job = d.get("job")
        if not job:
            return v.with_(icon=v.icon or "package", label=tr("ets2.no_job"), dim=0.2)
        return v.with_(value=str(job.get("dest_city") or "?")[:14], sub=str(job.get("cargo") or "")[:18], label="",
                       icon=v.icon or "package")
    if m == "nav":
        km = d.get("nav_distance_km")
        return v.with_(value=f"{km:.0f} km" if km else "--", sub=_fmt_min(d.get("nav_time_min")), mono=True,
                       label="", icon=v.icon or "navigation")
    if m == "rest":
        mins = d.get("rest_stop_min")
        tired = mins is not None and mins < 60
        return v.with_(value=_fmt_min(mins), sub=tr("ets2.rest"), mono=True, label="", icon=v.icon or "bed",
                       accent="#f59e0b" if tired else v.accent, anim="pulse" if tired else None)
    if m == "clock":
        return v.with_(value=str(d.get("game_clock") or "--"), sub=tr("ets2.clock"), mono=True, label="")
    if m == "retarder":
        lvl, mx = d.get("retarder") or 0, d.get("retarder_max") or 0
        return v.with_(value=f"{lvl}/{mx}" if mx else "--", sub=tr("ets2.retarder"), mono=True, label="",
                       active=lvl > 0, progress=(lvl / mx) if mx else 0, progress_style="bar")
    return v


# --------------------------------------------------------------------------- BeamNG.drive
BNG_METRICS = tuple((m, f"bng.{m}") for m in (
    "speed", "gear", "rpm", "fuel", "turbo", "eng_temp", "oil_temp", "pedals", "handbrake", "full_beam",
    "signal_left", "signal_right", "hazard", "abs", "tc", "shift_light", "engine"))
BNG_STATES = {"handbrake": "#ef4444", "full_beam": "#3b82f6", "signal_left": "#22c55e", "signal_right": "#22c55e",
              "hazard": "#f97316", "abs": "#f59e0b", "tc": "#f59e0b", "shift_light": "#ef4444"}


@widget("beamng", "games", "car-front", Field("metric", "choice", "speed", BNG_METRICS), needs=("beamng",))
def _beamng(e, s, c, v):
    d = e.beamng()
    m = c.get("metric", "speed")
    if not d.get("connected"):
        return v.with_(dim=0.5, value=None, icon=v.icon or "car-front", label=v.label or tr(f"bng.{m}"))
    if m in BNG_STATES:
        on = bool(d.get(m))
        return v.with_(active=on, accent=BNG_STATES[m] if on else v.accent,
                       anim="blink" if on and m in ("hazard", "shift_light") else None)
    if m == "engine":
        on = not d.get("battery_warn")
        return v.with_(active=on, accent="#22c55e" if on else v.accent)
    if m == "speed":
        return v.with_(value=f"{d.get('speed_kmh', 0):.0f}", sub="km/h", mono=True, label="")
    rpm = d.get("rpm") or 0
    frac = min(1.0, rpm / 7500)
    if m == "gear":
        return v.with_(value=str(d.get("gear", "N")), sub=f"{d.get('speed_kmh', 0):.0f} km/h", mono=True, label="",
                       progress=e.smooth(s, "rpm", frac, 0.5), progress_style="bar",
                       accent="#ef4444" if d.get("shift_light") else _load_color(frac))
    if m == "rpm":
        return v.with_(value=f"{rpm:.0f}", sub="RPM", mono=True, label="", progress=e.smooth(s, "rpm", frac, 0.5),
                       accent=_load_color(frac))
    if m == "fuel":
        f = d.get("fuel") or 0
        return v.with_(value=f"{f * 100:.0f}%", sub=tr("bng.fuel"), mono=True, label="", progress=f,
                       progress_style="bar", accent=_load_color(1 - f), anim="pulse" if f < 0.1 else None)
    if m == "turbo":
        return v.with_(value=f"{d.get('turbo_bar') or 0:+.2f}", sub="bar", mono=True, label="",
                       progress=max(0.0, min(1.0, (d.get("turbo_bar") or 0) / 2)))
    if m in ("eng_temp", "oil_temp"):
        t = d.get(m) or 0
        hot = t > (110 if m == "eng_temp" else 130)
        return v.with_(value=f"{t:.0f}°", sub=tr(f"bng.{m}"), mono=True, label="",
                       accent="#ef4444" if hot else v.accent, anim="alert" if hot else None)
    if m == "pedals":
        thr, brk = d.get("throttle") or 0, d.get("brake") or 0
        return v.with_(value=f"{thr * 100:.0f}", sub=f"{tr('bng.brake')} {brk * 100:.0f}", mono=True, label="",
                       progress=e.smooth(s, "thr", thr, 0.6), progress_style="bar",
                       accent="#ef4444" if brk > thr else "#22c55e")
    return v


def default_for(widget_id: str) -> dict[str, Any]:
    w = WIDGETS[widget_id]
    return {"type": widget_id, **{f.name: f.default for f in w.fields}}
