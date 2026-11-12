"""The engine: device events in, actions out, and a 30 fps animated picture of every key.

Runs on the Qt main thread. Device and worker threads talk to it only through queued signals.
Each frame it builds a :class:`Visual` per slot (static look + live widget + effects), and only
re-renders / re-sends the keys whose picture actually changed.
"""
from __future__ import annotations

import logging
import math
import os
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
from .render.painter import MONO_FONT, UI_FONT, ease_in_out, ease_out_cubic, load_fonts, picture_frame
from .settings import Settings
from . import style as looks
from .i18n import language as tr_language
from .i18n import tr
from .widgets import WIDGETS
from .notify import HttpBridge, NotifyCenter, source as notify_source, EVENT_COLOR

log = logging.getLogger(__name__)

FPS = 30
LONG_PRESS_S = 0.55
MENU_HOLD_S = 1.2          # hold any key this long -> page menu (works on every page)
MENU_TIMEOUT_S = 10.0
BANNER_S = 1.3
TRANSITION_S = 0.42
CHANGE_S = 0.28            # content change transition (value/icon/label), see style "change"
ANIM_FPS = 20              # animations advance at most this often per key (USB budget)
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


# Games whose controllers never touch the deck: while one runs the idle screensaver stays away.
GAME_PROCESSES = ("acs.exe", "acc.exe", "ac2-win64-shipping.exe", "flightsimulator.exe", "flightsimulator2024.exe",
                  "eurotrucks2.exe", "amtrucks.exe", "beamng.drive.x64.exe", "x-plane.exe", "dirtrally2.exe",
                  "f1_24.exe", "iracingsim64dx11.exe", "rfactor2.exe", "lmu.exe", "ams2avx.exe")
IDLE_DIM = 0.5          # idle screen brightness, relative to the user's setting
IDLE_DIM_MIN = 15


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
        self._looks: dict[str, dict] = {}            # merged style per slot key, from the last frame
        self._content: dict[str, tuple] = {}         # what each slot showed, to spot changes
        self._changes: dict[str, tuple] = {}         # key -> (old image, t0, mode) while cross-fading
        self._menu: dict | None = None          # {"t0", "sel", "offset"} while the page menu is open
        self._banner_t0: float = -1.0
        self._announce: dict | None = None       # strip-wide announcement (notifications)
        self.notify_center = NotifyCenter()
        self.attention_hooks: list = []          # callables(source, event, message) - lights, sounds...
        self._rgb_active = False
        self._http = HttpBridge(self, int((settings.notify or {}).get("http_port") or 0))
        self._swallow_up: int | None = None
        self._boot_t0: float = -1.0
        self._last_input = time.monotonic()
        self._screensaver = False
        self._asleep = False
        self._idle_triggered = False     # screensaver/sleep started by the idle timer (not by the user)
        self._mixer = None                       # soundboard, created when a page uses sounds
        self._no_cable_told = False
        self.audio: dict = {"level": None, "muted": None, "mic_muted": None}
        self._output_pending: tuple | None = None    # (Output, until) while the default output is switching
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
        self._http.start()
        self._watcher.start()
        self._tick.start()
        self._auto.start()

    def stop(self) -> None:
        self._http.stop()
        if self._mixer is not None:
            try:
                self.settings.soundboard["volume"] = self._mixer.cfg.get("volume", 0.8)
                self.settings.save()
                self._mixer.stop()
            except Exception:  # noqa: BLE001
                log.exception("soundboard shutdown")
        if "lights" in self._started:
            try:
                from .integrations.lighting import lights
                lights.restore()
                lights.shutdown()
            except Exception:  # noqa: BLE001
                log.exception("lights shutdown")
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
                self.audio = {"level": level, "muted": muted, "mic_muted": mic, "mic_level": osb.mic_level()}
            except Exception:  # noqa: BLE001
                log.debug("audio poll failed", exc_info=True)
            self._audio_poke.wait(0.6)
            self._audio_poke.clear()

    def poke_audio(self) -> None:
        time.sleep(0.03)
        self._audio_poke.set()

    # ------------------------------------------------------------------ default playback device
    def audio_outputs(self) -> tuple[list, str | None]:
        """([Output], default id) for widgets: the device being switched to counts as the default already,
        so the key answers the tap at once instead of after Windows is done."""
        outputs, current = osb.audio_outputs()
        pending = self._output_pending
        if pending and time.monotonic() < pending[1]:
            current = pending[0].id
        return outputs, current

    def output_switching(self, target) -> None:
        """Worker thread, right before the switch."""
        self._output_pending = (target, time.monotonic() + 4.0)

    def output_switched(self, target, ok: bool) -> None:
        """Worker thread, after the switch: toast, and the soundboard's "only me" sounds follow."""
        from .audio_outputs import title
        self._output_pending = None
        if not ok:
            self.call_in_ui(lambda: self.toast.emit(tr("audio_out.failed", name=title(target)[0])))
            return
        if self._mixer is not None and hasattr(self._mixer, "follow_output"):
            try:
                self._mixer.follow_output(target.name)
            except Exception:  # noqa: BLE001
                log.exception("soundboard could not follow the new output")
        self.poke_audio()
        self.call_in_ui(lambda: self.toast.emit("🔊 " + title(target)[0]))

    # ================================================================== device
    def _on_connected(self, dev: N4Device) -> None:
        self.device = dev
        dev.set_brightness(self.profile.brightness)
        self._sent.clear()
        self._boot_t0 = time.monotonic() if self.profile.animations else -1.0
        # Safety net: repaint everything once the link has settled. A panel that was handed back to its
        # firmware (previous FreeDock quit) can drop the very first frames; we only resend what changes.
        QTimer.singleShot(2500, self._repaint_all)
        self.device_changed.emit(dev.info.label + (f" · {dev.firmware}" if dev.firmware else ""))

    def _repaint_all(self) -> None:
        self._sent.clear()
        self._visuals.clear()

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
            if a.get("type") == "audio_output" and "skip" in slot.widget:
                a = {**a, "skip": slot.widget["skip"]}     # the key cycles the devices it shows
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
                if self._long_action(self._slot(ref)):
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
            self._run(self._long_action(self._slot(ref)))

    @staticmethod
    def _long_action(slot: Slot) -> dict | None:
        """The slot's hold action; a sound key holds to play the same sound elsewhere (default: headset)."""
        if slot.long_action:
            return slot.long_action
        a = slot.action or {}
        if a.get("type") == "sound" and a.get("sound") and a.get("hold", "local") != "none":
            return {"type": "sound", "sound": a["sound"], "target": a.get("hold", "local")}
        return None

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
            self._ss_last = -1.0
            if self.device and self.profile.brightness > IDLE_DIM_MIN:
                # dim the idle screen: easier on the eyes at night and on the LCDs over hours
                self.device.set_brightness(max(IDLE_DIM_MIN, round(self.profile.brightness * IDLE_DIM)))
        elif op == "reload":
            self._sent.clear()
            self._visuals.clear()

    def _wake(self) -> None:
        self._idle_triggered = False
        if self._screensaver and self.device:
            self.device.set_brightness(self.profile.brightness)
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

    # ------------------------------------------------------------------ soundboard
    def sound_library(self):
        from .soundboard.library import library
        return library()

    def _start_soundboard(self) -> None:
        try:
            lib = self.sound_library()
            lib.ensure_builtins(self.settings.language or "en")
            self.soundboard()
        except Exception:  # noqa: BLE001 - missing audio stack must not take the deck down
            log.exception("soundboard start failed")

    def soundboard(self):
        if self._mixer is None and os.environ.get("FREEDOCK_NO_AUDIO"):
            from .soundboard.mixer import NullMixer
            self._mixer = NullMixer(self.sound_library())
        if self._mixer is None:
            from .soundboard.mixer import Mixer
            self._mixer = Mixer(self.sound_library(), dict(self.settings.soundboard))
            self._mixer.start()
        return self._mixer

    def sound_playing(self) -> dict:
        return self._mixer.playing() if self._mixer is not None else {}

    def sound_play(self, sid: str | None, target: str = "mic") -> None:
        if not sid:
            return
        try:
            result = self.soundboard().play(sid, target)
        except Exception:  # noqa: BLE001
            log.exception("sound %s", sid)
            self.toast.emit(tr("sound.toast.failed"))
            return
        if result == "missing":
            self.toast.emit(tr("sound.toast.missing"))
        elif result == "no_cable" and not self._no_cable_told:
            self._no_cable_told = True
            self.toast.emit(tr("sound.toast.no_cable"))

    def soundboard_op(self, op: str, step: int = 5) -> None:
        m = self.soundboard()
        if op == "stop":
            m.stop_all()
        elif op in ("up", "down"):
            v = m.change_volume((step if op == "up" else -step) / 100)
            self.settings.soundboard["volume"] = round(v, 3)
            self.toast.emit(tr("sound.toast.volume", pct=f"{v * 100:.0f}"))
        elif op == "monitor":
            on = not m.cfg.get("monitor")
            m.set("monitor", on)
            self.settings.soundboard["monitor"] = on
            self.toast.emit(tr("sound.toast.monitor_on" if on else "sound.toast.monitor_off"))
        elif op == "passthrough":
            on = not m.cfg.get("passthrough", True)
            self.settings.soundboard["passthrough"] = on
            m.restart({"passthrough": on})
            self.toast.emit(tr("sound.toast.pass_on" if on else "sound.toast.pass_off"))

    def ai_usage(self) -> dict:
        from .integrations import ai
        return getattr(ai, "usage", None).data if hasattr(ai, "usage") else {}

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

    def game_bindings(self, game: str) -> dict:
        """Keyboard bindings of ETS2/ATS or BeamNG, re-read when the game's file changes."""
        cache = self.__dict__.setdefault("_game_bind_cache", {})
        try:
            if game == "ets2":
                from .integrations import ets2
                path = ets2.controls_path()
                key = (path, path.stat().st_mtime if path else 0)
                if cache.get(game, (None, None))[0] != key:
                    cache[game] = (key, ets2.read_bindings(path))
            elif game == "beamng":
                from .integrations import beamng
                udir = beamng.user_dir()
                diff = udir / "settings" / "inputmaps" / "keyboard.diff" if udir else None
                key = (diff, diff.stat().st_mtime if diff and diff.exists() else 0)
                if cache.get(game, (None, None))[0] != key:
                    cache[game] = (key, beamng.read_bindings())
            else:
                return {}
        except OSError:
            return cache.get(game, (None, {}))[1]
        return cache[game][1]

    @staticmethod
    def game_actions(game: str) -> dict:
        if game == "ets2":
            from .integrations.ets2 import ACTIONS
            return ACTIONS
        if game == "beamng":
            from .integrations.beamng import ACTIONS
            return ACTIONS
        return {}

    def ets2(self) -> dict:
        from .integrations.ets2 import telemetry
        return telemetry.data

    def beamng(self) -> dict:
        from .integrations.beamng import telemetry
        return telemetry.data

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
                    if a and a.get("type") == "lights":
                        needs.add("lights")
                    if a and a.get("type") in ("sound", "soundboard"):
                        needs.add("sound")
            if "ac_shift_lights" in page.effects:
                needs.add("assetto")
            if "rgb_shift_lights" in page.effects:
                needs.update(("lights", "assetto", "ets2", "beamng"))
        if (self.settings.notify or {}).get("lights", True):
            needs.add("lights")
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
        for game in ("ets2", "beamng"):
            if game in needs and game not in self._started:
                import importlib
                importlib.import_module(f".integrations.{game}", __package__).telemetry.start()
        if "lights" in needs and "lights" not in self._started:
            self._start_lights()
        if "sound" in needs and "sound" not in self._started and not os.environ.get("FREEDOCK_NO_AUDIO"):
            self._started.add("sound")
            threading.Thread(target=self._start_soundboard, name="soundboard-init", daemon=True).start()
        if "ai" in needs and "ai" not in self._started:
            try:
                from .integrations.ai import usage
                usage.start()
            except (ImportError, AttributeError):
                log.warning("AI usage reader unavailable")
        if "obs" in needs and "obs" not in self._started:
            from .integrations.obs import client
            o = self.settings.obs
            client().configure(o.get("host"), o.get("port"), o.get("password"))
            client().ensure()
        self._started |= needs

    # ================================================================== auto switch
    def _game_telemetry_live(self) -> bool:
        """True while a sim integration that is already running reports a connected game."""
        for name in ("assetto", "ets2", "beamng"):
            mod = sys.modules.get(f"{__package__}.integrations.{name}")
            if name in self._started and mod and mod.telemetry.data.get("connected"):
                return True
        mod = sys.modules.get(f"{__package__}.integrations.msfs")
        return bool("msfs" in self._started and mod and mod.sim.connected)

    def _keep_awake_for_games(self, gaming: bool) -> None:
        """Wheels, pedals and yokes never touch the deck, so a game in progress counts as activity.

        Only an idle-triggered screensaver/sleep is undone; one the user asked for stays.
        """
        if not gaming:
            return
        self._last_input = time.monotonic()
        if self._idle_triggered and (self._screensaver or self._asleep):
            self._wake()

    def _auto_switch(self) -> None:
        rules = [r for r in self.profile.auto_switch if r.get("process") and self.profile.page(r.get("page"))]
        try:
            running = {(p.info.get("name") or "").lower() for p in psutil.process_iter(["name"])}
        except psutil.Error:
            running = None
        known = {r.get("process", "").lower() for r in self.profile.auto_switch} | set(GAME_PROCESSES)
        self._keep_awake_for_games(bool(running and known & running) or self._game_telemetry_live())
        if not rules or running is None:
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
        st = looks.merged(self.profile.theme, self.page.style, slot.style)
        self._looks[ref.key] = st
        if slot.empty:
            v = EMPTY.with_(accent=st.get("accent") or self.page.accent)
        else:
            kw = looks.visual_kwargs(st)
            kw.setdefault("accent", self.page.accent)
            v = Visual(icon=slot.icon, label=slot.label, image=slot.image, **kw)
        wcfg = slot.widget or ({"type": "sound"} if (slot.action or {}).get("type") == "sound" else None)
        if wcfg and (w := WIDGETS.get(wcfg.get("type"))):
            try:
                v = w.fn(self, ref, wcfg, v)
            except Exception:  # noqa: BLE001 - a broken widget shows an error tile, never crashes
                log.exception("widget %s failed", slot.widget.get("type"))
                v = v.with_(icon="triangle-alert", label="widget", accent="#ef4444", active=True)
        if ref.kind == Kind.KEY and ref.index < 5 and "ac_shift_lights" in self.page.effects:
            v = self._shift_light(ref.index, v)
        return self._animate(ref, v, st)

    def _animate(self, ref: SlotRef, v: Visual, st: dict) -> Visual:
        """Apply the looks that depend on time: accent lock, animation phase, animated picture frame."""
        if st.get("accent_lock") and st.get("accent"):
            v = v.with_(accent=st["accent"])
        now = time.monotonic()
        anim = looks.animation_for(st, v.active, v.anim) if self.profile.animations else None
        if anim:
            period = looks.period(anim, st)
            steps = max(6, min(60, int(period * ANIM_FPS)))
            v = v.with_(anim=anim, anim_t=math.floor((now / period % 1.0) * steps) / steps)
        elif v.anim:
            v = v.with_(anim=None)
        if v.image:
            w, h = N4.size(ref.kind)
            frame = picture_frame(v.image, w, h, v.image_fit, now * 1000)
            if frame != v.frame:
                v = v.with_(frame=frame)
        return v

    def _content_change(self, ref: SlotRef, v: Visual, img: QImage, now: float) -> QImage:
        """Cross-fade / slide / zoom between the old and the new content of a slot."""
        sig = (v.value, v.label, v.icon, v.sub, v.image, v.badge)
        old_sig = self._content.get(ref.key)
        self._content[ref.key] = sig
        mode = self._looks.get(ref.key, {}).get("change") or "none"
        if (old_sig is not None and sig != old_sig and mode != "none" and self.profile.animations
                and ref.key in self.images):
            self._changes[ref.key] = (self.images[ref.key], now, mode)
        ch = self._changes.get(ref.key)
        if ch is None:
            return img
        old, t0, mode = ch
        t = (now - t0) / CHANGE_S
        if t >= 1:
            del self._changes[ref.key]
            return img
        self._visuals.pop(ref.key, None)          # keep rendering until the change is done
        t = ease_in_out(t)
        w, h = img.width(), img.height()
        out = QImage(w, h, QImage.Format.Format_RGB32)
        out.fill(QColor("#000000"))
        p = QPainter(out)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if mode == "slide":
            p.drawImage(0, int(-t * h * 0.6), old)
            p.setOpacity(t)
            p.drawImage(0, int((1 - t) * h * 0.6), img)
        elif mode == "zoom":
            p.setOpacity(1 - t)
            p.drawImage(0, 0, old)
            s = 0.82 + 0.18 * t
            p.setOpacity(t)
            p.translate(w / 2, h / 2)
            p.scale(s, s)
            p.drawImage(int(-w / 2), int(-h / 2), img)
        else:
            p.drawImage(0, 0, old)
            p.setOpacity(t)
            p.drawImage(0, 0, img)
        p.end()
        return out

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
            self._idle_triggered = True
        if "lights" in self._started:
            self._rgb_shift_lights(now)
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
        announcing = self._menu is None and self._announce is not None
        for ref in SLOTS:
            if (banner or announcing) and ref.kind == Kind.ZONE:
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
                self._content.pop(ref.key, None)
                self._changes.pop(ref.key, None)
            elif self._menu is not None:
                self._content.pop(ref.key, None)
            else:
                img = self._content_change(ref, v, img, now)
            self._output(ref.key, ref, img)
        if announcing and self._draw_announce(now):
            return
        if banner:
            self._draw_banner(now)

    # ------------------------------------------------------------------ notifications
    def notify_event(self, src: str, event: str, message: str = "", progress: float | None = None) -> None:
        """An outside event (AI assistant, script, webhook). UI thread."""
        attention = self.notify_center.apply(src, event, message, progress)
        log.info("notify %s %s %s", src, event, message[:80])
        if not attention:
            return
        meta = notify_source(src)
        text = tr(f"notify.{event}", name=meta.name)
        cfg = self.settings.notify or {}
        if cfg.get("banner", True):
            self.announce(text, message, EVENT_COLOR.get(event, meta.color), meta.icon)
        self.toast.emit(text + (f": {message}" if message else ""))
        for hook in list(self.attention_hooks):
            try:
                hook(src, event, message)
            except Exception:  # noqa: BLE001 - a broken light must not break notifications
                log.exception("attention hook failed")

    # ------------------------------------------------------------------ RGB lights
    def _start_lights(self) -> None:
        from .integrations.lighting import lights
        cfg = self.settings.lighting or {}
        lights.configure(strip_enabled=bool(cfg.get("strip", True)), strip_count=int(cfg.get("strip_count", 71)),
                         chroma_enabled=bool(cfg.get("chroma", True)))
        if self._light_hook not in self.attention_hooks:
            self.attention_hooks.append(self._light_hook)

    def _light_hook(self, src: str, event: str, message: str) -> None:
        if not (self.settings.notify or {}).get("lights", True):
            return
        from .integrations.lighting import lights
        h = EVENT_COLOR.get(event, notify_source(src).color).lstrip("#")
        lights.flash((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)), times=3)
        self._rgb_pause_until = time.monotonic() + 1.2      # let the flash play before shift lights resume

    def _rgb_shift_lights(self, now: float) -> None:
        """Rev bar on the LED strip / keyboard while a racing page with the effect is open."""
        on = "rgb_shift_lights" in self.page.effects
        frac = None
        if on:
            for get in (self.assetto, self.ets2, self.beamng):
                try:
                    d = get()
                except Exception:  # noqa: BLE001 - an integration that never started
                    continue
                if d.get("connected") and d.get("rpm") is not None:
                    top = d.get("max_rpm") or (2500 if get == self.ets2 else 7500)
                    frac = max(0.0, min(1.0, (d["rpm"] / top - 0.55) / 0.42))
                    break
        from .integrations.lighting import lights
        if frac is None:
            if self._rgb_active:
                lights.restore()
                self._rgb_active = False
            return
        if now < getattr(self, "_rgb_pause_until", 0) or now - getattr(self, "_rgb_last", 0) < 1 / 25:
            return
        self._rgb_last = now
        self._rgb_active = True
        lights.bar(round(frac, 3))

    def ack_notify(self, src: str | None = None) -> None:
        self.notify_center.ack(src)

    def announce(self, text: str, sub: str = "", color: str = "#5b8cff", icon: str | None = None,
                 seconds: float = 3.2) -> None:
        """Show a message across the whole touch strip for a few seconds."""
        self._announce = {"text": text, "sub": sub, "color": color, "icon": icon, "t0": time.monotonic(),
                          "dur": seconds}
        self._wake()

    def _draw_announce(self, now: float) -> bool:
        a = self._announce
        if a is None:
            return False
        t = (now - a["t0"]) / a["dur"]
        if t >= 1:
            self._announce = None
            for i in range(N4.zone_count):
                self._visuals.pop(f"zone{i}", None)
                self._content.pop(f"zone{i}", None)
            return False
        from .render.painter import icon_image
        alpha = min(1.0, t * 8) * min(1.0, (1 - t) * 6)
        zw, zh = N4.zone_size
        canvas = QImage(zw * 4, zh, QImage.Format.Format_RGB32)
        canvas.fill(QColor("#000000"))
        p = QPainter(canvas)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
        p.setOpacity(alpha)
        color = QColor(a["color"])
        g = QLinearGradient(0, 0, canvas.width(), 0)
        g.setColorAt(0, color.darker(420))
        g.setColorAt(0.5, color.darker(200))
        g.setColorAt(1, color.darker(420))
        p.fillRect(QRectF(0, 0, canvas.width(), zh), g)
        # light sweep, twice per announcement
        x = (t * 2 % 1.0) * canvas.width() * 1.4 - canvas.width() * 0.2
        sweep = QLinearGradient(x - 120, 0, x + 120, 0)
        sweep.setColorAt(0, QColor(255, 255, 255, 0))
        sweep.setColorAt(0.5, QColor(255, 255, 255, 60))
        sweep.setColorAt(1, QColor(255, 255, 255, 0))
        p.fillRect(QRectF(0, 0, canvas.width(), zh), sweep)
        left = zh * 0.35
        if a.get("icon") and (ic := icon_image(a["icon"], "#ffffff", int(zh * 0.5))):
            pulse = 1 + 0.08 * math.sin(now * 9)
            p.save()
            p.translate(left + ic.width() / 2, zh / 2)
            p.scale(pulse, pulse)
            p.drawImage(int(-ic.width() / 2), int(-ic.height() / 2), ic)
            p.restore()
            left += ic.width() + zh * 0.25
        f = QFont(UI_FONT)
        f.setPixelSize(int(zh * (0.32 if a.get("sub") else 0.40)))
        f.setWeight(QFont.Weight.Black)
        p.setFont(f)
        p.setPen(QColor("#ffffff"))
        text_rect = QRectF(left, 0, canvas.width() - left - zh * 0.3, zh * (0.58 if a.get("sub") else 1.0))
        p.drawText(text_rect, int(Qt.AlignmentFlag.AlignLeft | (Qt.AlignmentFlag.AlignBottom if a.get("sub")
                                                                else Qt.AlignmentFlag.AlignVCenter)), a["text"])
        if a.get("sub"):
            f.setPixelSize(int(zh * 0.2))
            f.setWeight(QFont.Weight.Medium)
            p.setFont(f)
            p.setPen(QColor(255, 255, 255, 210))
            sub = p.fontMetrics().elidedText(a["sub"], Qt.TextElideMode.ElideRight, int(text_rect.width()))
            p.drawText(QRectF(left, zh * 0.62, text_rect.width(), zh * 0.3),
                       int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop), sub)
        p.end()
        for i in range(N4.zone_count):
            self._output(f"zone{i}", SlotRef(Kind.ZONE, i), canvas.copy(i * zw, 0, zw, zh))
        return True

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
        """Boot animation made of the keys themselves (see render/boot.py)."""
        from .render import boot
        t = now - self._boot_t0
        if t >= boot.DURATION:
            self._boot_t0 = -1.0
            self._transition = Transition(now, 1, {})
            self._visuals.clear()
            return False
        for i in range(N4.key_count):
            self._output(f"key{i}", SlotRef(Kind.KEY, i), boot.key_tile(i, t, N4.key_size))
        zw, zh = N4.zone_size
        strip = boot.strip_canvas(t, zw * N4.zone_count, zh)
        for i in range(N4.zone_count):
            self._output(f"zone{i}", SlotRef(Kind.ZONE, i), strip.copy(i * zw, 0, zw, zh))
        return True

    def _draw_screensaver(self, now: float) -> None:
        # Two frames a second is enough for a clock and keeps the USB link nearly idle for hours
        # (each frame re-uploads every tile whose picture changed).
        if now - getattr(self, "_ss_last", -1.0) < 0.5:
            return
        self._ss_last = now
        t = now - getattr(self, "_ss_t0", now)
        if self.profile.idle_mode != "clock":
            self._draw_aurora(t)
            return
        from .render import idle
        stats = self.stats if "stats" in self._started else {}
        alerts = []
        for sid in self.notify_center.pending():
            src, st = notify_source(sid), self.notify_center.get(sid)
            alerts.append((EVENT_COLOR.get(st.status, src.color), src.icon, src.name,
                           tr(f"ai.{st.status}") if st.status in ("done", "question", "error") else ""))
        info = idle.IdleInfo(now=time.localtime(), lang=tr_language(), weather=self.weather_data() or {},
                             cpu=stats.get("cpu") if stats else None, ram=stats.get("ram") if stats else None,
                             alerts=alerts)
        for i, img in enumerate(idle.keys(info, t, N4.key_size, N4.key_count)):
            self._output(f"key{i}", SlotRef(Kind.KEY, i), img)
        for i, img in enumerate(idle.zones(info, t, N4.zone_size, N4.zone_count)):
            self._output(f"zone{i}", SlotRef(Kind.ZONE, i), img)

    def _draw_aurora(self, t: float) -> None:
        def paint(p: QPainter, w: int, h: int):
            g = QLinearGradient(0, 0, w, h)
            for k in range(4):
                hue = (t * 6 + k * 70) % 360
                g.setColorAt(k / 3, QColor.fromHsv(int(hue), 200, 90 + int(30 * math.sin(t / 3 + k))))
            p.fillRect(QRectF(0, 0, w, h), g)

        self._draw_canvas(paint)
        for i in range(N4.zone_count):
            self._output(f"zone{i}", SlotRef(Kind.ZONE, i), render(EMPTY, *N4.zone_size))
