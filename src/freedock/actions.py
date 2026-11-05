"""Actions: what a key, strip zone, knob turn or swipe does.

Each action type declares its fields (used by the editor to build a form) and a ``run`` function.
OS-level actions run on a worker thread so a slow program launch never freezes the deck;
deck-level actions (pages, brightness, timers) are routed back to the engine.
"""
from __future__ import annotations

import json
import logging
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .platform import os_backend as osb

if TYPE_CHECKING:
    from .engine import Engine

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Field:
    name: str
    kind: str                    # text | keys | int | float | choice | page | file | bool | multiline | actions | ac_binding
    default: Any = None
    options: tuple = ()          # for choice: (value, label key) pairs


@dataclass(frozen=True)
class ActionType:
    id: str
    category: str
    icon: str
    fields: tuple[Field, ...] = ()
    deck: bool = False           # runs on the engine (UI thread) instead of the worker
    run: Callable[["Engine", dict], None] = field(default=lambda e, a: None)


REGISTRY: dict[str, ActionType] = {}


def action(id: str, category: str, icon: str, *fields: Field, deck: bool = False):
    def wrap(fn):
        REGISTRY[id] = ActionType(id, category, icon, tuple(fields), deck, fn)
        return fn
    return wrap


# --------------------------------------------------------------------------- system
@action("hotkey", "system", "keyboard", Field("keys", "keys", ""),
        Field("hold_ms", "int", 30))
def _hotkey(engine, a):
    osb.press_combo(a.get("keys", ""), int(a.get("hold_ms", 30)))


@action("text", "system", "type", Field("text", "multiline", ""))
def _text(engine, a):
    osb.type_text(a.get("text", ""))


@action("open", "system", "external-link", Field("target", "text", "https://"))
def _open(engine, a):
    if a.get("target"):
        osb.open_target(a["target"])


@action("launch", "system", "rocket", Field("path", "file", ""), Field("args", "text", ""))
def _launch(engine, a):
    if a.get("path"):
        osb.launch(a["path"], a.get("args", ""))


@action("command", "system", "terminal", Field("command", "text", ""))
def _command(engine, a):
    if a.get("command"):
        osb.run_command(a["command"])


@action("scroll", "system", "mouse", Field("steps", "int", 1), Field("horizontal", "bool", False))
def _scroll(engine, a):
    osb.scroll(int(a.get("steps", 1)), bool(a.get("horizontal")))


# --------------------------------------------------------------------------- media / audio
@action("media", "media", "play", Field("op", "choice", "playpause", (
        ("playpause", "media.playpause"), ("nexttrack", "media.next"), ("prevtrack", "media.prev"),
        ("stop", "media.stop"))))
def _media(engine, a):
    osb.press_combo([a.get("op", "playpause")])


@action("volume", "media", "volume-2", Field("op", "choice", "up", (
        ("up", "volume.up"), ("down", "volume.down"), ("mute", "volume.mute"), ("set", "volume.set"))),
        Field("step", "int", 4))
def _volume(engine, a):
    op, step = a.get("op", "up"), int(a.get("step", 4)) / 100
    if op == "mute":
        osb.toggle_mute()
    elif op == "set":
        osb.set_volume(step)
    else:
        osb.change_volume(step if op == "up" else -step)
    engine.poke_audio()


@action("mic", "media", "mic", Field("op", "choice", "toggle", (("toggle", "mic.toggle"),)))
def _mic(engine, a):
    osb.toggle_mic()
    engine.poke_audio()


# --------------------------------------------------------------------------- deck
@action("page", "deck", "layers", Field("target", "page", "next"), deck=True)
def _page(engine, a):
    engine.goto(a.get("target", "next"))


@action("brightness", "deck", "sun", Field("op", "choice", "up", (
        ("up", "brightness.up"), ("down", "brightness.down"), ("set", "brightness.set"))),
        Field("step", "int", 10), deck=True)
def _brightness(engine, a):
    engine.change_brightness(a.get("op", "up"), int(a.get("step", 10)))


@action("deck", "deck", "monitor", Field("op", "choice", "sleep", (
        ("sleep", "deck.sleep"), ("screensaver", "deck.screensaver"), ("reload", "deck.reload"))), deck=True)
def _deck(engine, a):
    engine.deck_op(a.get("op", "sleep"))


@action("timer", "deck", "timer", Field("op", "choice", "toggle", (
        ("toggle", "timer.toggle"), ("reset", "timer.reset"))), deck=True)
def _timer(engine, a):
    engine.timer_op(a.get("_slot"), a.get("op", "toggle"))


@action("multi", "deck", "list-ordered", Field("steps", "actions", []), Field("delay_ms", "int", 80))
def _multi(engine, a):
    delay = int(a.get("delay_ms", 80)) / 1000
    for i, step in enumerate(a.get("steps") or []):
        if i:
            time.sleep(delay)
        run_now(engine, step)


@action("delay", "deck", "hourglass", Field("ms", "int", 500))
def _delay(engine, a):
    time.sleep(int(a.get("ms", 500)) / 1000)


# --------------------------------------------------------------------------- web / apps
@action("webhook", "web", "webhook", Field("url", "text", "https://"), Field("method", "choice", "POST", (
        ("GET", "GET"), ("POST", "POST"), ("PUT", "PUT"))), Field("body", "multiline", ""))
def _webhook(engine, a):
    body = a.get("body") or None
    req = urllib.request.Request(a.get("url", ""), method=a.get("method", "POST"),
                                 data=body.encode() if body else None,
                                 headers={"Content-Type": "application/json", "User-Agent": "FreeDock"})
    try:
        with urllib.request.urlopen(req, timeout=8):  # noqa: S310 - user-configured URL
            pass
    except Exception as exc:  # noqa: BLE001
        log.warning("webhook failed: %s", exc)


@action("obs", "web", "video", Field("op", "choice", "record", (
        ("record", "obs.record"), ("stream", "obs.stream"), ("scene", "obs.scene"), ("mute", "obs.mute"),
        ("replay", "obs.replay"))), Field("name", "text", ""), deck=True)
def _obs(engine, a):
    from .integrations.obs import client
    c = client()
    op, name = a.get("op", "record"), a.get("name", "")
    if op == "record":
        c.request("ToggleRecord")
    elif op == "stream":
        c.request("ToggleStream")
    elif op == "replay":
        c.request("SaveReplayBuffer")
    elif op == "scene" and name:
        c.request("SetCurrentProgramScene", {"sceneName": name})
    elif op == "mute" and name:
        c.request("ToggleInputMute", {"inputName": name})


# --------------------------------------------------------------------------- games
@action("ac_binding", "games", "car", Field("binding", "ac_binding", "ACTION_HEADLIGHTS"))
def _ac_binding(engine, a):
    b = engine.ac_bindings().get(a.get("binding", ""))
    if b is None:
        log.warning("Assetto Corsa binding %s has no keyboard key in controls.ini", a.get("binding"))
        engine.notify_missing_binding(a.get("binding", ""))
        return
    osb.press_combo(b.combo, 60)


@action("sim_event", "games", "plane", Field("event", "text", "AP_MASTER"), Field("value", "int", 0))
def _sim_event(engine, a):
    from .integrations.msfs import sim
    if not sim.send_event(a.get("event", ""), int(a.get("value", 0))):
        log.info("SimConnect not connected; %s dropped", a.get("event"))


# --------------------------------------------------------------------------- dispatch
def run_now(engine: "Engine", a: dict | None) -> None:
    """Run an action synchronously on the current thread (deck actions hop to the UI thread)."""
    if not a:
        return
    t = REGISTRY.get(a.get("type", ""))
    if t is None:
        log.warning("unknown action %s", a.get("type"))
        return
    if t.deck:
        engine.call_in_ui(lambda: t.run(engine, a))
        return
    try:
        t.run(engine, a)
    except Exception:  # noqa: BLE001 - one broken action must not stop the deck
        log.exception("action %s failed", json.dumps(a)[:200])


def describe(a: dict | None) -> str:
    """Short human text for an action, used in the editor and tooltips."""
    from .i18n import tr
    from .platform.keys import pretty
    if not a:
        return tr("action.none")
    t = a.get("type", "")
    if t == "hotkey":
        return pretty(a.get("keys", ""))
    if t == "page":
        return tr("action.page") + ": " + str(a.get("target", ""))
    if t == "ac_binding":
        return "AC: " + a.get("binding", "")
    if t == "sim_event":
        return "SIM: " + a.get("event", "")
    if t in ("open", "launch", "command"):
        return tr(f"action.{t}") + ": " + str(a.get("target") or a.get("path") or a.get("command") or "")
    return tr(f"action.{t}")
