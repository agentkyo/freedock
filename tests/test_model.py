import json
import textwrap

from freedock.integrations.assetto import read_bindings
from freedock.platform.keys import parse_combo, pretty
from freedock.presets import build_default_profile
from freedock.profile import Profile


def test_parse_combo():
    assert parse_combo("Ctrl+Shift+Esc") == ["ctrl", "shift", "esc"]
    assert parse_combo("win++") == ["win", "plus"]
    assert parse_combo("Super+period") == ["win", "period"]
    assert parse_combo("") == []
    assert pretty("ctrl+shift+s") == "Ctrl+Shift+S"


def test_default_profile_round_trip():
    for lang in ("en", "pt", "es"):
        prof = build_default_profile(lang)
        assert [p.id for p in prof.pages] == ["daily", "windows", "assetto", "msfs", "msfs2", "ai", "ets2", "ets2_trip", "beamng", "sounds",
                                              "cs2", "ets2_bus"]
        again = Profile.from_dict(json.loads(json.dumps(prof.to_dict())))
        assert again.to_dict() == prof.to_dict()
        for p in again.pages:
            assert len(p.keys) == 10 and len(p.zones) == 4 and len(p.knobs) == 4


def test_auto_switch_targets_exist():
    prof = build_default_profile("en")
    for rule in prof.auto_switch:
        assert prof.page(rule["page"]) is not None


def test_profile_save_load(tmp_path):
    prof = build_default_profile("pt")
    path = prof.save(tmp_path / "p.json")
    loaded = Profile.load(path)
    assert loaded.page("assetto").effects == ["ac_shift_lights", "rgb_shift_lights"]
    assert loaded.page("windows").platform == "windows"


def test_assetto_bindings(tmp_path):
    ini = tmp_path / "controls.ini"
    ini.write_text(textwrap.dedent("""
        [ABS]
        JOY=-1
        KEY=0x41
        [ACTION_HORN]
        KEY=0x48
        [__EXT_HAZARDS]
        KEY=0x28
        KEY_MODIFICATOR=18
        [TCUP]
        JOY=0
        BUTTON=21
        KEY=-1
    """), encoding="utf-8")
    b = read_bindings(ini)
    assert b["ABS"].combo == ["ctrl", "vk:0x41"]          # AC implies Ctrl for assists
    assert b["ACTION_HORN"].combo == ["vk:0x48"]
    assert b["__EXT_HAZARDS"].combo == ["alt", "vk:0x28"]  # CSP stores the modifier
    assert "TCUP" not in b                                # wheel-only binding


def test_streamdock_import(tmp_path):
    from freedock.importers.streamdock import import_profiles
    prof = tmp_path / "ABC.sdProfile"
    prof.mkdir()
    (prof / "manifest.json").write_text(json.dumps({
        "DeviceUUID": "MBox-N4", "Name": "Test",
        "Actions": {
            "0,0": {"UUID": "com.hotspot.streamdock.system.hotkey", "Name": "Hotkey",
                    "Settings": {"Hotkeys": [{"KeyCmd": True, "VKeyCode": 68}, {"VKeyCode": -1}]},
                    "States": [{"Image": "x.mbg"}]},
            "1,1": {"UUID": "com.hotspot.streamdock.system.website", "Settings": {"path": "https://example.com"}},
            "2,2": {"UUID": "com.hotspot.streamdock.system.volume", "Settings": {}},
        }}), encoding="utf-8")
    pages = import_profiles(tmp_path)
    assert len(pages) == 1
    p = pages[0]
    assert p.keys[0].action == {"type": "hotkey", "keys": "win+d"}
    assert p.keys[6].action == {"type": "open", "target": "https://example.com"}
    assert p.zones[2].widget == {"type": "volume"}
