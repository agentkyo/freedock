"""Navigation must work from every page, whatever its knobs are bound to."""
import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication.instance() or QGuiApplication([])

from freedock.device import EventType, InputEvent, Kind  # noqa: E402
from freedock.engine import MENU_HOLD_S, Engine  # noqa: E402
from freedock.presets import build_default_profile  # noqa: E402
from freedock.settings import Settings  # noqa: E402


def _pump(seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        QCoreApplication.processEvents()
        time.sleep(0.01)


def _engine() -> Engine:
    e = Engine(build_default_profile("en"), Settings(language="en"))
    e.profile.animations = False
    return e


def test_hold_any_key_opens_menu_on_every_page():
    e = _engine()
    for page in e.visible_pages():
        e.goto(page.id)
        # keys with their own hold action (sound keys: hold = headset) keep it; any other key opens the menu
        k = next((i for i, s in enumerate(page.keys) if not e._long_action(s)), None)
        assert k is not None, f"{page.id}: no key left to hold for the page menu"
        e._on_input(InputEvent(Kind.KEY, k, EventType.DOWN))
        _pump(MENU_HOLD_S + 0.3)
        assert e._menu is not None, page.id
        # releasing the key that opened the menu must not pick anything
        e._on_input(InputEvent(Kind.KEY, k, EventType.UP))
        assert e._menu is not None and e.page.id == page.id
        e._on_input(InputEvent(Kind.ZONE, 0, EventType.TAP))     # "Back"
        assert e._menu is None


def test_menu_pick_by_key_and_by_knob():
    e = _engine()
    pages = e.visible_pages()
    e.open_menu()
    e._on_input(InputEvent(Kind.KEY, 2, EventType.DOWN))
    e._on_input(InputEvent(Kind.KEY, 2, EventType.UP))
    assert e.page.id == pages[2].id and e._menu is None
    e.open_menu()
    e._on_input(InputEvent(Kind.KNOB, 1, EventType.ROTATE, 1))
    e._on_input(InputEvent(Kind.KNOB, 1, EventType.PUSH))
    assert e.page.id == pages[3].id


def test_swipe_leaves_game_pages():
    e = _engine()
    for page_id in ("assetto", "msfs"):
        e.goto(page_id)
        e._on_input(InputEvent(Kind.SWIPE, 0, EventType.SWIPE, -1))
        e._on_input(InputEvent(Kind.SWIPE, 0, EventType.SWIPE, -1))
        _pump(0.2)     # actions run on the worker thread, deck actions hop back to the UI thread
        assert e.page.id != page_id


def test_menu_draws_every_page():
    e = _engine()
    from freedock.engine import SlotRef
    e.open_menu()
    e._on_tick()
    labels = [e._menu_visual(SlotRef(Kind.KEY, i)).label for i in range(len(e.visible_pages()))]
    assert labels == [p.name for p in e.visible_pages()]
    assert len(e.images) == 14


def test_running_game_keeps_idle_screensaver_away():
    """Wheels and yokes never touch the deck: a game in progress must count as activity."""
    e = _engine()
    e.profile.idle_minutes = 1
    e._last_input = time.monotonic() - 120
    e._on_tick()
    assert e._screensaver and e._idle_triggered
    e._keep_awake_for_games(True)               # a game (or live telemetry) shows up
    assert not e._screensaver and time.monotonic() - e._last_input < 1
    e._on_tick()
    assert not e._screensaver


def test_game_does_not_undo_a_sleep_the_user_asked_for():
    e = _engine()
    e.deck_op("sleep")
    e._keep_awake_for_games(True)
    assert e._asleep
    e._keep_awake_for_games(False)
    assert e._asleep


def test_live_values_swap_without_animation():
    """A key whose value ticks every second (speed, game clock) must not slide/fade on every tick."""
    from PySide6.QtGui import QImage

    from freedock.engine import CHANGE_CALM_S, SLOTS
    from freedock.render.painter import Visual
    e = _engine()
    e.profile.animations = True
    ref = SLOTS[0]
    e._looks[ref.key] = {"change": "slide"}
    img = QImage(8, 8, QImage.Format.Format_RGB32)
    e.images[ref.key] = img
    t = 1000.0
    e._content_change(ref, Visual(value="1"), img, t)
    e._content_change(ref, Visual(value="2"), img, t + CHANGE_CALM_S + 1)     # after a calm spell: animates
    assert ref.key in e._changes
    e._changes.clear()
    e._content_change(ref, Visual(value="3"), img, t + CHANGE_CALM_S + 2)     # 1 s later: live data, swap
    assert ref.key not in e._changes
    e._content_change(ref, Visual(value="3"), img, t + 3 * CHANGE_CALM_S)     # unchanged: nothing
    e._content_change(ref, Visual(value="4"), img, t + 5 * CHANGE_CALM_S)     # calm again: animates
    assert ref.key in e._changes
