"""Game lighting: the keyboard, mouse, headset and the ambilight strip follow the game you play.

Layers, bottom to top:
  1. the game's theme (scenes.THEMES, picked from the foreground process)
  2. live game state: CS2 health / ammo / C4 countdown, truck blinkers, rev bars
  3. short effects on game events: kills, headshots, the bomb, round won / lost, flashbangs
Notification flashes from FreeDock (AI assistants...) still play over all of it.

The strip is the user's ambilight while gaming, so by default (strip mode "events") a scene only
takes it for the moments that matter - the C4 countdown, a flashbang, a kill, the round end, shift
lights - and gives it back right after. "theme" keeps the theme on it the whole time, "off" never
touches it. Games that drive Razer Chroma themselves (native_chroma) keep the keyboard; FreeDock then
only adds the strip.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Callable

from . import scenes
from .scenes import Effect, Frame, Theme

log = logging.getLogger(__name__)

FPS = 25.0
DEFAULTS = {"enabled": True, "strip": "events", "native_chroma": [], "off": []}


class GameLights:
    def __init__(self, lights=None, clock: Callable[[], float] = time.monotonic, fps: float = FPS) -> None:
        if lights is None:
            from .lighting import lights as default_lights
            lights = default_lights
        self.lights = lights
        self.clock = clock
        self.interval = 1.0 / fps
        self.cfg = dict(DEFAULTS)
        self.theme: Theme | None = None
        self.forced: Theme | None = None          # "lights" action op=theme, until restore
        self.effects: list[Effect] = []
        self._cs2_cursor = 0
        self._last = -1e9
        self._last_frame: tuple | None = None
        self._driving = False
        self._native_released = False

    # -- configuration / game detection (UI thread)

    def configure(self, cfg: dict | None) -> None:
        self.cfg = {**DEFAULTS, **(cfg or {})}
        if not self.cfg.get("enabled", True):
            self.theme = None

    @property
    def enabled(self) -> bool:
        return bool(self.cfg.get("enabled", True))

    def set_games(self, running: set[str], foreground: str | None) -> Theme | None:
        """Called every couple of seconds with the running process names. Returns the active theme."""
        theme = scenes.theme_for(running, foreground) if self.enabled else None
        if theme is not None and theme.id in set(self.cfg.get("off") or []):
            theme = None
        if (theme and theme.id) != (self.theme and self.theme.id):
            log.info("game lights: %s", theme.name if theme else "no game")
            self.effects.clear()
            self._native_released = False
        self.theme = theme
        return theme

    def force(self, theme_id: str | None) -> bool:
        """Show a theme now, game or not (preview / a key). None ends it."""
        self.forced = scenes.THEMES.get(theme_id or "")
        self._last_frame = None
        return self.forced is not None or theme_id is None

    def native(self, theme: Theme | None) -> bool:
        native = set(self.cfg.get("native_chroma") or [])
        return bool(theme and (theme.native_chroma or theme.id in native or "*" in native))

    # -- events

    def add_effect(self, effect: Effect | None) -> None:
        if effect is not None:
            self.effects = [e for e in self.effects if not e.done(effect.t0)][-6:] + [effect]

    def _pull_cs2(self, cs2, now: float) -> dict | None:
        if cs2 is None:
            return None
        d = cs2.data
        self._cs2_cursor, events = cs2.events_since(self._cs2_cursor)
        for _t, name, info in events:
            self.add_effect(scenes.cs2_effect(name, info, now, d.get("team")))
        return d

    # -- frames (UI thread, every engine tick)

    def tick(self, now: float | None = None, *, shift: float | None = None, n_leds: int = 71,
             cs2=None, truck: dict | None = None, paused: bool = False) -> bool:
        """Draw one frame if one is due. Returns True while a scene owns any light."""
        now = self.clock() if now is None else now
        theme = self.forced or self.theme
        d = self._pull_cs2(cs2, now) if theme is not None and theme.id == "cs2" else None
        if cs2 is not None and d is None:
            self._cs2_cursor = cs2.events_since(self._cs2_cursor)[0]   # skip events of a game not shown
        self.effects = [e for e in self.effects if not e.done(now)]
        if theme is None and shift is None and not self.effects:
            self._stop()
            return False
        if paused:                                     # a notification flash is playing over us
            self._last_frame = None                    # ...and changed the lights: resend after it
            return self._driving
        if now - self._last < self.interval:
            return self._driving
        self._last = now
        frame = self._compose(theme, now, shift, n_leds, d, truck)
        self._send(theme, frame)
        return True

    def _compose(self, theme: Theme | None, now: float, shift: float | None, n_leds: int, cs2: dict | None,
                 truck: dict | None) -> Frame:
        mode = self.cfg.get("strip", "events")
        strip_always = mode == "theme"
        if theme is None:
            frame = Frame.blank(None)
        elif theme.id == "cs2" and cs2 is not None:
            frame = scenes.cs2_frame(theme, cs2, now, n_leds, strip_always)
        else:
            frame = scenes.render_theme(theme, now, n_leds if strip_always else None)
        if truck and truck.get("connected"):
            left = truck.get("blinker_left_lamp") or (truck.get("hazard") and truck.get("blinker_right_lamp"))
            right = truck.get("blinker_right_lamp") or (truck.get("hazard") and truck.get("blinker_left_lamp"))
            scenes.blinker_overlay(frame, bool(left), bool(right), n_leds if strip_always else None,
                                   beacon=bool(truck.get("beacon")), now=now)
        if shift is not None:
            scenes.shift_overlay(frame, shift, n_leds, now)
        if self.effects:
            if frame.strip is None and any(e.strip for e in self.effects):
                frame.strip = [scenes.BLACK] * n_leds if mode != "off" else None
            for e in self.effects:
                e.apply(frame, now)
        if mode == "off":
            frame.strip = None
        return frame

    def _send(self, theme: Theme | None, frame: Frame) -> None:
        native = self.native(theme)
        keyboard = None if native else frame.keyboard
        glow = None if native else frame.glow
        if native and not self._native_released:
            self.lights.restore("chroma")              # the game's own Chroma effects take over
            self._native_released = True
        key = (tuple(map(tuple, keyboard)) if keyboard else None, tuple(frame.strip) if frame.strip else None, glow)
        if key == self._last_frame:
            return                                     # static scene: nothing new to send
        self._last_frame = key
        self.lights.scene(keyboard, frame.strip, glow)
        self._driving = True

    def _stop(self) -> None:
        if self._driving:
            self.lights.end_scene()
            self._driving = False
            self._last_frame = None

    def shutdown(self) -> None:
        self.theme = self.forced = None
        self.effects.clear()
        self._stop()
