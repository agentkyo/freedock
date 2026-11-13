"""Companion API: /info, /page and /context on the loopback bridge (used by MyWheel)."""
import json
import urllib.error
import urllib.request

import pytest

from freedock.integrations.companion import Companion, hotkeys
from freedock.notify import HttpBridge
from freedock.presets import build_default_profile


class FakeEngine:
    """The slice of Engine the companion touches."""

    def __init__(self):
        self.profile = build_default_profile("en")
        self.page = self.profile.page("daily")
        self.gotos = []

    def visible_pages(self):
        return self.profile.pages

    def goto(self, target, direction=0, auto=False):
        self.gotos.append((target, auto))
        self.page = self.profile.page(target)

    def call_in_ui(self, fn):
        fn()


def test_bus_page_ships_and_uses_the_coach_bindings():
    prof = build_default_profile("pt")
    bus = prof.page("ets2_bus")
    assert bus is not None and bus.name == "Ônibus"
    assert bus.keys[9].action == {"type": "ets2_binding", "binding": "entrdoors"}
    assert prof.page("ets2").keys[9].action["binding"] == "attach"      # the truck page is untouched


def test_context_follows_the_vehicle_only_between_sibling_pages():
    e = FakeEngine()
    c = Companion(e)
    assert c.set_context("ets2", "bus") is False and e.gotos == []     # on Daily: leave the deck alone
    e.page = e.profile.page("ets2")
    c.context.clear()
    assert c.set_context("ets2", "bus", "Scania Touring", "mywheel") is True
    assert e.page.id == "ets2_bus" and e.gotos == [("ets2_bus", True)]
    assert c.set_context("ets2", "bus") is False                        # repeated: no-op
    e.page = e.profile.page("ets2")                                     # player picks the truck page by hand
    assert c.set_context("ets2", "bus") is False and e.page.id == "ets2"
    e.page = e.profile.page("ets2_bus")
    assert c.set_context("ets2", "truck") is True and e.page.id == "ets2"


def test_hotkeys_lists_every_combo_including_multi_steps():
    prof = build_default_profile("en")
    page = prof.page("daily")
    page.keys[0].action = {"type": "hotkey", "keys": "ctrl+b"}
    page.keys[1].action = {"type": "multi", "steps": [{"type": "hotkey", "keys": "y"}]}
    found = hotkeys(prof)
    assert "ctrl+b" in found and "y" in found


@pytest.fixture
def bridge():
    e = FakeEngine()
    b = HttpBridge(e, port=47899)
    b.start()
    yield e
    b.stop()


def _get(path):
    with urllib.request.urlopen(f"http://127.0.0.1:47899{path}", timeout=3) as r:
        return json.loads(r.read())


def test_http_info_page_context(bridge):
    info = _get("/info")
    assert info["app"] == "freedock" and info["api"] == 1 and info["page"]["id"] == "daily"
    assert any(p["id"] == "ets2_bus" for p in info["pages"])
    assert _get("/page?id=ets2") == {"ok": True} and bridge.page.id == "ets2"
    assert _get("/context?game=ets2&vehicle=bus&source=mywheel") == {"ok": True}
    assert bridge.page.id == "ets2_bus"
    assert _get("/info")["context"]["ets2"]["vehicle"] == "bus"
    with pytest.raises(urllib.error.HTTPError) as err:
        _get("/page?id=nope")
    assert err.value.code == 404
    with pytest.raises(urllib.error.HTTPError) as err:
        _get("/context?game=ets2")
    assert err.value.code == 400


def test_context_that_arrived_early_is_applied_when_the_game_page_opens():
    e = FakeEngine()
    c = Companion(e)
    c.set_context("ets2", "bus")                     # deck on Daily: remembered, not applied
    assert e.page.id == "daily"
    assert c.resolve("ets2") == "ets2_bus"           # auto-switch asks before opening the truck page
    assert c.resolve("ets2_trip") == "ets2_trip" and c.resolve("daily") == "daily"
    assert c.siblings_of("ets2") == {"ets2_bus"} and c.siblings_of("daily") == set()
    c.forget()                                        # game closed
    assert c.resolve("ets2") == "ets2"


def test_usb_restart_script_targets_the_composite_device():
    from freedock.device import usbreset
    from freedock.device.layout import N4
    assert usbreset.instance_pattern(N4) == r"USB\VID_6602&PID_1001\*"
    script = usbreset.restart_script(N4)
    assert "pnputil.exe /restart-device" in script and "VID_6602&PID_1001" in script
