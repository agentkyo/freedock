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
