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
                   progress_style="bar", active=t.running or blink,
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
                   label="")


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
                   label=tr("widget.mic.muted") if muted else tr("widget.mic.live"))


@widget("page_name", "deck", "layers")
def _page_name(e, s, c, v):
    pages = e.visible_pages()
    idx = next((i for i, p in enumerate(pages) if p.id == e.page.id), 0)
    dots = "".join("●" if i == idx else "○" for i in range(len(pages)))
    return v.with_(value=e.page.name, sub=dots, icon=e.page.icon or v.icon, label="")


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


def default_for(widget_id: str) -> dict[str, Any]:
    w = WIDGETS[widget_id]
    return {"type": widget_id, **{f.name: f.default for f in w.fields}}
