"""The engine: device events in, actions out, and a 30 fps animated picture of every key.

Runs on the Qt main thread. Device and worker threads talk to it only through queued signals.
Each frame it builds a :class:`Visual` per slot (static look + live widget + effects), and only
re-renders / re-sends the keys whose picture actually changed.
"""
from __future__ import annotations

import logging
import math
import queue
import sys
import threading
import time
from dataclasses import dataclass, field

import psutil
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QImage, QLinearGradient, QPainter
from PySide6.QtCore import QRectF, Qt

from . import actions as act
from .device import N4, DeviceWatcher, EventType, InputEvent, Kind, N4Device
from .platform import NAME as PLATFORM
from .platform import os_backend as osb
from .profile import Page, Profile, Slot
from .render import Fx, Visual, render, to_device_jpeg
from .render.painter import MONO_FONT, UI_FONT, ease_in_out, ease_out_cubic, load_fonts
from .settings import Settings
from .i18n import tr
from .widgets import WIDGETS

log = logging.getLogger(__name__)

FPS = 30
LONG_PRESS_S = 0.55
MENU_HOLD_S = 1.2          # hold any key this long -> page menu (works on every page)
MENU_TIMEOUT_S = 10.0
BANNER_S = 1.3
TRANSITION_S = 0.42
EMPTY = Visual(bg="#0a0c11", fg="#3a4252")


@dataclass(frozen=True)
class SlotRef:
    kind: Kind
    index: int

    @property
    def key(self) -> str:
        return f"{self.kind.value}{self.index}"


SLOTS = [SlotRef(Kind.KEY, i) for i in range(N4.key_count)] + [SlotRef(Kind.ZONE, i) for i in range(N4.zone_count)]


@dataclass
class Timer:
    total: float
    started: bool = False
    running: bool = False
    acc: float = 0.0
    since: float = 0.0

    def elapsed(self) -> float:
        return self.acc + (time.monotonic() - self.since if self.running else 0.0)

    def remaining(self) -> float:
        return self.total - self.elapsed()

    def toggle(self) -> None:
        if self.running:
            self.acc += time.monotonic() - self.since
            self.running = False
        else:
            if self.total and self.remaining() <= 0:
                self.acc = 0.0
            self.since, self.running, self.started = time.monotonic(), True, True

    def reset(self) -> None:
        self.started = self.running = False
        self.acc = 0.0


@dataclass
class SlotFx:
    press_target: float = 0.0
    press: float = 0.0
    ripple_t0: float = -1.0
    flash_t0: float = -1.0
    down_at: float = 0.0
    long_fired: bool = False
    error_until: float = 0.0


@dataclass
class Transition:
    t0: float
    direction: int
    old: dict[str, QImage] = field(default_factory=dict)


class Engine(QObject):
    frame = Signal(str, QImage)              # slot key ("key0", "zone3"), picture (unrotated)
    page_changed = Signal(str)
    device_changed = Signal(str)             # human status text
    toast = Signal(str)
    _ui_call = Signal(object)
    _dev_event = Signal(object)
    _dev_connected = Signal(object)
    _dev_lost = Signal()

    def __init__(self, profile: Profile, settings: Settings):
        super().__init__()
        load_fonts()
        self.profile = profile
        self.settings = settings
        self.lang = settings.language
        self.device: N4Device | None = None
        self.page: Page = self._first_page()
        self._history: list[str] = []
        self._auto_page: str | None = None      # page chosen by auto-switch
        self._auto_return: str | None = None
        self._manual_override = False
        self._visuals: dict[str, Visual | None] = {}
        self._fxstate: dict[str, tuple] = {}
        self.images: dict[str, QImage] = {}
        self._sent: dict[str, bytes] = {}
        self._fx: dict[str, SlotFx] = {s.key: SlotFx() for s in SLOTS}
        self._timers: dict[str, Timer] = {}
        self._smooth: dict[tuple[str, str], float] = {}
        self._transition: Transition | None = None
        self._menu: dict | None = None          # {"t0", "sel", "offset"} while the page menu is open
        self._banner_t0: float = -1.0
        self._swallow_up: int | None = None
        self._boot_t0: float = -1.0
        self._last_input = time.monotonic()
        self._screensaver = False
        self._asleep = False
        self.audio: dict = {"level": None, "muted": None, "mic_muted": None}
        self._audio_poke = threading.Event()
        self._started: set[str] = set()
        self._ac_bindings: dict = {}
        self._ac_mtime = -1.0
        self._jobs: queue.Queue = queue.Queue()

        self._ui_call.connect(lambda fn: fn())
        self._dev_event.connect(self._on_input)
        self._dev_connected.connect(self._on_connected)
        self._dev_lost.connect(self._on_lost)

        threading.Thread(target=self._worker, name="actions", daemon=True).start()
        threading.Thread(target=self._audio_loop, name="audio", daemon=True).start()
        self._watcher = DeviceWatcher(lambda d: self._dev_connected.emit(d), lambda: self._dev_lost.emit(),
                                      lambda e: self._dev_event.emit(e), settings.device_serial)
        self._tick = QTimer(self)
        self._tick.setInterval(int(1000 / FPS))
        self._tick.timeout.connect(self._on_tick)
        self._auto = QTimer(self)
        self._auto.setInterval(2000)
        self._auto.timeout.connect(self._auto_switch)
        self._start_integrations()

    # ================================================================== lifecycle
    def start(self) -> None:
        self._watcher.start()
        self._tick.start()
        self._auto.start()

    def stop(self) -> None:
        self._tick.stop()
        self._auto.stop()
        self._watcher.stop()
        self.device = None

    def reload(self, profile: Profile | None = None) -> None:
        """Profile edited in the GUI: keep the current page if it still exists."""
        if profile is not None:
            self.profile = profile
        self.page = self.profile.page(self.page.id) or self._first_page()
        self._visuals.clear()
        self._start_integrations()
        if self.device:
            self.device.set_brightness(self.profile.brightness)

    def _first_page(self) -> Page:
        pages = self.visible_pages()
        if not pages:
            p = Page.new("Page 1")
            self.profile.pages.append(p)
            return p
        return self.profile.page(self.profile.start_page) or pages[0]

    def visible_pages(self) -> list[Page]:
        return [p for p in self.profile.pages if p.available_on(PLATFORM)]

    # ================================================================== threads -> UI
    def call_in_ui(self, fn) -> None:
        self._ui_call.emit(fn)

    def _worker(self) -> None:
        while True:
            a = self._jobs.get()
            act.run_now(self, a)

    def _audio_loop(self) -> None:
        while True:
            try:
                level, muted = osb.volume()
                mic = osb.mic_state()
                self.audio = {"level": level, "muted": muted, "mic_muted": mic}
            except Exception:  # noqa: BLE001
                log.debug("audio poll failed", exc_info=True)
            self._audio_poke.wait(0.6)
            self._audio_poke.clear()

    def poke_audio(self) -> None:
        time.sleep(0.03)
        self._audio_poke.set()

    # ================================================================== device
    def _on_connected(self, dev: N4Device) -> None:
        self.device = dev
        dev.set_brightness(self.profile.brightness)
        self._sent.clear()
        self._boot_t0 = time.monotonic() if self.profile.animations else -1.0
        self.device_changed.emit(dev.info.label + (f" · {dev.firmware}" if dev.firmware else ""))

    def _on_lost(self) -> None:
        self.device = None
        self.device_changed.emit("")

    # ================================================================== input
    def _slot(self, ref: SlotRef) -> Slot:
        return (self.page.keys if ref.kind == Kind.KEY else self.page.zones)[ref.index]

    def _press_action(self, ref: SlotRef) -> dict | None:
        slot = self._slot(ref)
        a = slot.action
        if not a and slot.widget and (w := WIDGETS.get(slot.widget.get("type"))) and w.press:
            a = w.press
        if a and a.get("type") == "timer":
            a = {**a, "_slot": ref}
        return a

    def _run(self, a: dict | None) -> None:
        if a:
            self._jobs.put(a)

    def _on_input(self, ev: InputEvent) -> None:
        now = time.monotonic()
        self._last_input = now
        if self._screensaver or self._asleep:
            # first touch only wakes the deck up
            self._wake()
            if ev.type in (EventType.DOWN, EventType.TAP, EventType.PUSH, EventType.SWIPE):
                self._swallow_up = ev.index if ev.kind == Kind.KEY else None
            return
        if self._menu is not None:
            self._menu_input(ev, now)
            return
        if ev.kind == Kind.KEY:
            ref = SlotRef(Kind.KEY, ev.index)
            fx = self._fx[ref.key]
            if ev.type == EventType.DOWN:
                fx.press_target, fx.down_at, fx.long_fired = 1.0, now, False
                if self._slot(ref).long_action:
                    QTimer.singleShot(int(LONG_PRESS_S * 1000), lambda r=ref, t=now: self._long_press(r, t))
                QTimer.singleShot(int(MENU_HOLD_S * 1000), lambda r=ref, t=now: self._menu_hold(r, t))
            elif ev.type == EventType.UP:
                fx.press_target = 0.0
                if self._swallow_up == ev.index:
                    self._swallow_up = None
                    return
                if not fx.long_fired:
                    fx.ripple_t0 = now
                    self._run(self._press_action(ref))
        elif ev.kind == Kind.ZONE:
            ref = SlotRef(Kind.ZONE, ev.index)
            self._fx[ref.key].ripple_t0 = now
            self._run(self._press_action(ref))
        elif ev.kind == Kind.KNOB:
            knob = self.page.knobs[ev.index]
            zfx = self._fx[f"zone{ev.index}"]
            if ev.type == EventType.ROTATE:
                zfx.flash_t0 = now
                self._run(knob.left if ev.value < 0 else knob.right)
            else:
                zfx.ripple_t0 = now
                self._run(knob.press or self._press_action(SlotRef(Kind.ZONE, ev.index)))
        elif ev.kind == Kind.SWIPE:
            a = self.page.swipe_left if ev.value < 0 else self.page.swipe_right
            self._run(a or {"type": "page", "target": "next" if ev.value < 0 else "prev"})

    # ------------------------------------------------------------------ page menu
    def _menu_hold(self, ref: SlotRef, t: float) -> None:
        fx = self._fx[ref.key]
        if fx.press_target > 0 and fx.down_at == t and not fx.long_fired and self._menu is None:
            fx.long_fired = True               # the release must not run the key's action
            self._swallow_up = ref.index
            self.open_menu()

    def _menu_pages(self) -> list[Page]:
        return self.visible_pages()

    def open_menu(self) -> None:
        pages = self._menu_pages()
        cur = next((i for i, p in enumerate(pages) if p.id == self.page.id), 0)
        self._menu = {"t0": time.monotonic(), "sel": cur, "offset": (cur // 10) * 10, "last": time.monotonic()}
        if self.profile.animations:
            self._transition = Transition(time.monotonic(), 1, dict(self.images))
        self._visuals.clear()

    def close_menu(self, animate: bool = True) -> None:
        if self._menu is None:
            return
        self._menu = None
        if animate and self.profile.animations:
            self._transition = Transition(time.monotonic(), -1, dict(self.images))
        self._visuals.clear()

    def _menu_pick(self, index: int) -> None:
        pages = self._menu_pages()
        if 0 <= index < len(pages):
            target = pages[index].id
            self.close_menu(animate=target == self.page.id)
            self.goto(target)

    def _menu_input(self, ev: InputEvent, now: float) -> None:
        m = self._menu
        m["last"] = now
        pages = self._menu_pages()
        if ev.kind == Kind.KEY:
            fx = self._fx[f"key{ev.index}"]
            if ev.type == EventType.DOWN:
                fx.press_target = 1.0
                return
            fx.press_target = 0.0
            if self._swallow_up == ev.index:      # release of the key that opened the menu
                self._swallow_up = None
                return
            self._menu_pick(m["offset"] + ev.index)
        elif ev.kind == Kind.ZONE:
            self._fx[f"zone{ev.index}"].ripple_t0 = now
            if ev.index == 0:
                self.close_menu()
            elif ev.index == 1:
                start = self.profile.page(self.profile.start_page) or (pages[0] if pages else None)
                if start:
                    self._menu_pick(pages.index(start) if start in pages else 0)
            else:
                self._menu_pick(m["sel"])
        elif ev.kind == Kind.KNOB:
            if ev.type == EventType.ROTATE:
                m["sel"] = (m["sel"] + ev.value) % max(1, len(pages))
                m["offset"] = (m["sel"] // 10) * 10
                self._visuals.clear()
            else:
                self._menu_pick(m["sel"])
        elif ev.kind == Kind.SWIPE:
            if len(pages) > 10:
                m["offset"] = max(0, min(((len(pages) - 1) // 10) * 10, m["offset"] + (10 if ev.value < 0 else -10)))
                m["sel"] = m["offset"]
                self._visuals.clear()
            else:
                self.close_menu()

    def _menu_visual(self, ref: SlotRef) -> Visual:
        m = self._menu
        pages = self._menu_pages()
        if ref.kind == Kind.KEY:
            i = m["offset"] + ref.index
            if i >= len(pages):
                return EMPTY
            p = pages[i]
            accent = p.accent or "#5b8cff"
            base = QColor(accent).darker(420).name()
            return Visual(bg=base, bg2="#05070b", accent=accent, icon=p.icon or "layers", label=p.name,
                          active=p.id == self.page.id, top_band="#ffffff" if i == m["sel"] else None,
                          badge=str(i + 1))
        hints = [("undo-2", tr("menu.back")), ("house", tr("menu.home")),
                 ("rotate-cw", tr("menu.turn")), ("pointer", tr("menu.push"))]
        icon, label = hints[ref.index]
        return Visual(bg="#11141c", bg2="#07090d", accent="#5b8cff", icon=icon, label=label,
                      fg="#e6e9f0" if ref.index < 2 else "#8b93a7")

    def _long_press(self, ref: SlotRef, t: float) -> None:
        fx = self._fx[ref.key]
        if fx.press_target > 0 and fx.down_at == t:
            fx.long_fired = True
            fx.flash_t0 = time.monotonic()
            self._run(self._slot(ref).long_action)

    def notify_missing_binding(self, binding: str) -> None:
        self.call_in_ui(lambda: self.toast.emit(tr("ui.missing_binding", name=binding)))

    # ================================================================== deck actions
    def goto(self, target: str, direction: int | None = None, auto: bool = False) -> None:
        if target == "menu":
            self.close_menu() if self._menu is not None else self.open_menu()
            return
        if self._menu is not None:
            self.close_menu(animate=False)
        pages = self.visible_pages()
        if not pages:
            return
        cur = next((i for i, p in enumerate(pages) if p.id == self.page.id), 0)
        if target == "next":
            new, direction = pages[(cur + 1) % len(pages)], direction or 1
        elif target == "prev":
            new, direction = pages[(cur - 1) % len(pages)], direction or -1
        elif target == "back":
            if not self._history:
                return
            new = self.profile.page(self._history.pop()) or pages[0]
            direction = direction or -1
        else:
            new = self.profile.page(target)
            if new is None:
                return
            idx = next((i for i, p in enumerate(pages) if p.id == new.id), cur)
            direction = direction or (1 if idx >= cur else -1)
        if new.id == self.page.id:
            return
        if not auto:
            self._manual_override = self._auto_page is not None
        if target != "back":
            self._history = (self._history + [self.page.id])[-20:]
        if self.profile.animations:
            self._transition = Transition(time.monotonic(), direction, dict(self.images))
        self.page = new
        self._visuals.clear()
        self._banner_t0 = time.monotonic()
        self.page_changed.emit(new.id)

    def change_brightness(self, op: str, step: int) -> None:
        b = self.profile.brightness
        b = step if op == "set" else b + (step if op == "up" else -step)
        self.profile.brightness = max(5, min(100, b))
        if self.device:
            self.device.set_brightness(self.profile.brightness)
        self.toast.emit(f"☀ {self.profile.brightness}%")

    def deck_op(self, op: str) -> None:
        if op == "sleep":
            self._asleep = True
            if self.device:
                self.device.set_brightness(0)
        elif op == "screensaver":
            self._screensaver = True
            self._ss_t0 = time.monotonic()
        elif op == "reload":
            self._sent.clear()
            self._visuals.clear()

    def _wake(self) -> None:
        self._screensaver = False
        if self._asleep:
            self._asleep = False
            if self.device:
                self.device.set_brightness(self.profile.brightness)
        self._visuals.clear()
        self._sent.clear()

    def timer(self, ref: SlotRef, total: float) -> Timer:
        k = f"{self.page.id}:{ref.key}"
        t = self._timers.get(k)
        if t is None or t.total != total:
            t = self._timers[k] = Timer(total)
        return t

    def timer_op(self, ref: SlotRef | None, op: str) -> None:
        if ref is None:
            return
        slot = self._slot(ref)
        total = 0.0
        if slot.widget and slot.widget.get("type") == "pomodoro":
            total = max(1, int(slot.widget.get("minutes", 25))) * 60
        t = self.timer(ref, total)
        t.reset() if op == "reset" else t.toggle()

    # ================================================================== data for widgets
    @property
    def stats(self) -> dict:
        from .integrations.system_stats import stats
        return stats.data

    def weather_data(self) -> dict:
        from .integrations.system_stats import weather
        return weather.poll(self.settings.weather.get("lat"), self.settings.weather.get("lon"))

    def assetto(self) -> dict:
        from .integrations.assetto import telemetry
        return telemetry.data

    def sim(self):
        from .integrations.msfs import sim
        return sim

    def ac_bindings(self) -> dict:
        from .integrations.assetto import controls_ini_path, read_bindings
        path = controls_ini_path(self.settings.assetto_controls)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return {}
        if mtime != self._ac_mtime:
            self._ac_bindings, self._ac_mtime = read_bindings(path), mtime
        return self._ac_bindings

    def smooth(self, ref: SlotRef, name: str, target: float, speed: float = 0.25) -> float:
        k = (ref.key, name)
        cur = self._smooth.get(k, target)
        cur += (target - cur) * speed
        if abs(target - cur) < 0.002:
            cur = target
        self._smooth[k] = cur
        return cur

    def _start_integrations(self) -> None:
        needs: set[str] = set()
        for page in self.profile.pages:
            for s in page.keys + page.zones:
                if s.widget and (w := WIDGETS.get(s.widget.get("type"))):
                    needs.update(w.needs)
            for s in page.keys + page.zones + [k for k in page.knobs]:
                for a in ([s.action, s.long_action] if isinstance(s, Slot) else [s.left, s.right, s.press]):
                    if a and a.get("type") == "obs":
                        needs.add("obs")
                    if a and a.get("type") == "sim_event":
                        needs.add("msfs")
            if "ac_shift_lights" in page.effects:
                needs.add("assetto")
        if "stats" in needs and "stats" not in self._started:
            from .integrations.system_stats import stats
            stats.start()
        if "assetto" in needs and "assetto" not in self._started:
            from .integrations.assetto import telemetry
            telemetry.start()
        if "msfs" in needs and "msfs" not in self._started:
            from .integrations.msfs import sim
            sim.override = self.settings.simconnect_dll
            sim.start()
        if "obs" in needs and "obs" not in self._started:
            from .integrations.obs import client
            o = self.settings.obs
            client().configure(o.get("host"), o.get("port"), o.get("password"))
            client().ensure()
        self._started |= needs

    # ================================================================== auto switch
    def _auto_switch(self) -> None:
        rules = [r for r in self.profile.auto_switch if r.get("process") and self.profile.page(r.get("page"))]
        if not rules:
            return
        try:
            running = {(p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])}
        except psutil.Error:
            return
        hit = next((r for r in rules if r["process"].lower() in running), None)
        if hit:
            if self._auto_page != hit["page"]:
                self._auto_page, self._manual_override = hit["page"], False
                self._auto_return = self.page.id
                self.goto(hit["page"], auto=True)
                self.toast.emit("▶ " + (self.profile.page(hit["page"]).name))
        elif self._auto_page:
            back = self._auto_return
            self._auto_page = self._auto_return = None
            if back and not self._manual_override:
                self.goto(back, auto=True)

    # ================================================================== rendering
    def _visual(self, ref: SlotRef) -> Visual:
        if self._menu is not None:
            return self._menu_visual(ref)
        slot = self._slot(ref)
        st = slot.style
        if slot.empty:
            v = EMPTY.with_(accent=self.page.accent)
        else:
            v = Visual(bg=st.get("bg", "#161a24"), bg2=st.get("bg2"), fg=st.get("fg", "#f5f7fb"),
                       accent=st.get("accent", self.page.accent), icon=slot.icon, label=slot.label,
                       image=slot.image)
        if slot.widget and (w := WIDGETS.get(slot.widget.get("type"))):
            try:
                v = w.fn(self, ref, slot.widget, v)
            except Exception:  # noqa: BLE001 - a broken widget shows an error tile, never crashes
                log.exception("widget %s failed", slot.widget.get("type"))
                v = v.with_(icon="triangle-alert", label="widget", accent="#ef4444", active=True)
        if ref.kind == Kind.KEY and ref.index < 5 and "ac_shift_lights" in self.page.effects:
            v = self._shift_light(ref.index, v)
        return v

    def _shift_light(self, i: int, v: Visual) -> Visual:
        d = self.assetto()
        if not d.get("connected") or d.get("max_rpm", 0) <= 0:
            return v
        frac = d["rpm"] / d["max_rpm"]
        start = 0.80
        thresholds = [start + i * 0.035 for i in range(5)]
        if frac >= 0.97:   # limiter: whole row blinks blue
            return v.with_(top_band="#3b82f6" if int(time.time() * 12) % 2 else "#0b1a3a")
        if frac >= thresholds[i]:
            return v.with_(top_band=["#22c55e", "#22c55e", "#facc15", "#f97316", "#ef4444"][i])
        return v

    def _effects(self, key: str, now: float) -> Fx:
        fx = self._fx[key]
        fx.press += (fx.press_target - fx.press) * (0.6 if fx.press_target > fx.press else 0.35)
        if abs(fx.press - fx.press_target) < 0.02:
            fx.press = fx.press_target
        ripple = -1.0
        if fx.ripple_t0 >= 0:
            t = (now - fx.ripple_t0) / 0.45
            if t >= 1:
                fx.ripple_t0 = -1.0
            else:
                ripple = ease_out_cubic(t)
        flash = 0.0
        if fx.flash_t0 >= 0:
            t = (now - fx.flash_t0) / 0.25
            if t >= 1:
                fx.flash_t0 = -1.0
            else:
                flash = 0.35 * (1 - t)
        if not self.profile.animations:
            return Fx(press=fx.press_target * 0.6)
        return Fx(press=fx.press, ripple=ripple, flash=flash)

    def _on_tick(self) -> None:
        now = time.monotonic()
        if self.profile.idle_minutes and not self._screensaver and not self._asleep and \
                now - self._last_input > self.profile.idle_minutes * 60:
            if self.profile.idle_mode == "off":
                self.deck_op("sleep")
            else:
                self.deck_op("screensaver")
        if self._asleep:
            return
        if self._screensaver:
            self._draw_screensaver(now)
            return
        if self._boot_t0 >= 0:
            if self._draw_boot(now):
                return
        if self._menu is not None and now - self._menu["last"] > MENU_TIMEOUT_S:
            self.close_menu()
        trans = self._transition
        if trans and now - trans.t0 > TRANSITION_S + 0.25:
            self._transition = trans = None
        banner = self._menu is None and self._banner_t0 >= 0 and now - self._banner_t0 < BANNER_S
        if self._banner_t0 >= 0 and not banner and self._menu is None:
            self._banner_t0 = -1.0
            for i in range(N4.zone_count):          # banner over: redraw the real zones
                self._visuals.pop(f"zone{i}", None)
        for ref in SLOTS:
            if banner and ref.kind == Kind.ZONE:
                continue
            w, h = N4.size(ref.kind)
            v = self._visual(ref)
            fx = self._effects(ref.key, now)
            fxkey = (fx.press, fx.ripple, fx.flash)
            if trans is None and self._visuals.get(ref.key) == v and self._fxstate.get(ref.key) == fxkey \
                    and ref.key in self.images:
                continue
            self._visuals[ref.key] = v
            self._fxstate[ref.key] = fxkey
            img = render(v, w, h, fx)
            if trans is not None:
                img = self._compose_transition(ref, trans, img, now)
                self._visuals.pop(ref.key, None)   # keep re-rendering until the slide finishes
            self._output(ref.key, ref, img)
        if banner:
            self._draw_banner(now)

    def _draw_banner(self, now: float) -> None:
        """Page name across the whole strip right after a page change, so you always know where you are."""
        t = (now - self._banner_t0) / BANNER_S
        alpha = min(1.0, t * 6) * min(1.0, (1 - t) * 4)
        zw, zh = N4.zone_size
        canvas = QImage(zw * 4, zh, QImage.Format.Format_RGB32)
        canvas.fill(QColor("#000000"))
        p = QPainter(canvas)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        accent = QColor(self.page.accent or "#5b8cff")
        g = QLinearGradient(0, 0, canvas.width(), 0)
        dark = accent.darker(380)
        g.setColorAt(0, QColor("#05070b"))
        g.setColorAt(0.5, dark)
        g.setColorAt(1, QColor("#05070b"))
        p.setOpacity(alpha)
        p.fillRect(QRectF(0, 0, canvas.width(), zh), g)
        f = QFont(UI_FONT)
        f.setPixelSize(int(zh * 0.42))
        f.setWeight(QFont.Weight.Black)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        p.drawText(QRectF(0, 0, canvas.width(), zh * 0.78), Qt.AlignmentFlag.AlignCenter, self.page.name)
        pages = self.visible_pages()
        idx = next((i for i, pg in enumerate(pages) if pg.id == self.page.id), 0)
        dot_w = 14
        x0 = (canvas.width() - len(pages) * dot_w * 2) / 2
        for i in range(len(pages)):
            c = accent if i == idx else QColor(255, 255, 255, 70)
            p.setBrush(c)
            p.setPen(Qt.PenStyle.NoPen)
            p.drawEllipse(QRectF(x0 + i * dot_w * 2 + dot_w / 2, zh * 0.80, 8, 8))
        p.end()
        for i in range(N4.zone_count):
            self._output(f"zone{i}", SlotRef(Kind.ZONE, i), canvas.copy(i * zw, 0, zw, zh))
        self._visuals.pop("zone0", None)

    def _compose_transition(self, ref: SlotRef, trans: Transition, new: QImage, now: float) -> QImage:
        old = trans.old.get(ref.key)
        col = ref.index % 5 if ref.kind == Kind.KEY else ref.index
        if trans.direction < 0:
            col = (4 if ref.kind == Kind.KEY else 3) - col
        t = (now - trans.t0 - col * 0.055) / (TRANSITION_S - 0.15)
        t = ease_in_out(max(0.0, min(1.0, t)))
        if old is None or t >= 1:
            return new
        w, h = new.width(), new.height()
        out = QImage(w, h, QImage.Format.Format_RGB32)
        out.fill(QColor("#000000"))
        p = QPainter(out)
        d = trans.direction
        p.setOpacity(1 - t * 0.6)
        p.drawImage(int(-d * t * w), 0, old)
        p.setOpacity(1.0)
        p.drawImage(int(d * (1 - t) * w), 0, new)
        p.end()
        return out

    def _output(self, key: str, ref: SlotRef, img: QImage) -> None:
        self.images[key] = img
        self.frame.emit(key, img)
        if self.device is not None:
            data = to_device_jpeg(img, N4.rotation)
            if self._sent.get(key) != data:
                self._sent[key] = data
                self.device.set_image(N4.image_id(ref.kind, ref.index), data)

    # ------------------------------------------------------------------ full-panel scenes
    def _draw_canvas(self, paint) -> None:
        """Paint one 5x2 canvas spanning all keys (plus the strip), then slice it into tiles."""
        kw, kh = N4.key_size
        gap = 18
        canvas = QImage(5 * kw + 4 * gap, 2 * kh + gap, QImage.Format.Format_RGB32)
        canvas.fill(QColor("#000000"))
        p = QPainter(canvas)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        paint(p, canvas.width(), canvas.height())
        p.end()
        for i in range(N4.key_count):
            r, c = divmod(i, 5)
            tile = canvas.copy(c * (kw + gap), r * (kh + gap), kw, kh)
            self._output(f"key{i}", SlotRef(Kind.KEY, i), tile)

    def _draw_boot(self, now: float) -> bool:
        t = (now - self._boot_t0) / 1.6
        if t >= 1:
            self._boot_t0 = -1.0
            self._transition = Transition(now, 1, {})
            self._visuals.clear()
            return False

        def paint(p: QPainter, w: int, h: int):
            sweep = ease_in_out(min(1.0, t * 1.4)) * (w + 260) - 130
            g = QLinearGradient(sweep - 220, 0, sweep + 60, h)
            g.setColorAt(0, QColor(0, 0, 0))
            g.setColorAt(0.6, QColor("#3b82f6"))
            g.setColorAt(0.85, QColor("#a855f7"))
            g.setColorAt(1, QColor(0, 0, 0))
            p.fillRect(QRectF(0, 0, w, h), g)
            f = QFont(UI_FONT)
            f.setPixelSize(int(h * 0.36))
            f.setWeight(QFont.Weight.Black)
            p.setFont(f)
            p.setOpacity(min(1.0, max(0.0, (t - 0.15) * 2.5)))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, "FreeDock")

        self._draw_canvas(paint)
        for i in range(N4.zone_count):
            zt = max(0.0, min(1.0, (t - 0.1 * i) * 2))
            v = Visual(bg="#000000", bg2="#1e3a8a" if zt > 0.5 else "#000000", accent="#a855f7")
            self._output(f"zone{i}", SlotRef(Kind.ZONE, i), render(v, *N4.zone_size, Fx(flash=0.4 * (1 - zt))))
        return True

    def _draw_screensaver(self, now: float) -> None:
        if int(now * FPS) % 3:   # 10 fps is plenty for a slow aurora
            return
        t = now - getattr(self, "_ss_t0", now)
        clock = time.strftime("%H:%M")

        def paint(p: QPainter, w: int, h: int):
            g = QLinearGradient(0, 0, w, h)
            for k in range(4):
                hue = (t * 12 + k * 70) % 360
                g.setColorAt(k / 3, QColor.fromHsv(int(hue), 200, 120 + int(40 * math.sin(t + k))))
            p.fillRect(QRectF(0, 0, w, h), g)
            if self.profile.idle_mode == "clock":
                f = QFont(MONO_FONT)
                f.setPixelSize(int(h * 0.62))
                f.setWeight(QFont.Weight.Bold)
                p.setFont(f)
                p.setPen(QColor(255, 255, 255, 235))
                p.drawText(QRectF(0, 0, w, h), Qt.AlignmentFlag.AlignCenter, clock)

        self._draw_canvas(paint)
        for i in range(N4.zone_count):
            self._output(f"zone{i}", SlotRef(Kind.ZONE, i), render(EMPTY, *N4.zone_size))
