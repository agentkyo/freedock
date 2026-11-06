import json
import struct

import pytest

from freedock.integrations import beamng

FMT = "<I4sHbB7f2I3f16s16s"     # LFS OutGauge as declared in BeamNG's outgauge.lua


def _packet(gear=3, show=0, with_id=True) -> bytes:
    pkt = struct.pack(FMT, 0, b"beam", beamng.OG_KM | beamng.OG_BAR, gear, 0,
                      20.0, 3500.0, 0.8, 90.0, 0.5, 0.0, 105.0, 0x7F7, show, 0.75, 0.25, 0.0, b"", b"")
    return pkt + struct.pack("<i", 7) if with_id else pkt


def test_packet_size():
    assert struct.calcsize(FMT) == 92 and len(_packet()) == 96


def test_decode_packet_values():
    d = beamng.decode_packet(_packet())
    assert d["connected"] and d["car"] == "beam" and d["gear"] == "2" and d["id"] == 7
    assert abs(d["speed_kmh"] - 72.0) < 1e-4 and d["rpm"] == 3500.0
    assert abs(d["turbo_bar"] - 0.8) < 1e-6 and d["eng_temp"] == 90.0 and d["oil_temp"] == 105.0
    assert d["fuel"] == 0.5 and d["throttle"] == 0.75 and d["brake"] == 0.25 and d["clutch"] == 0.0
    assert not any(d[k] for k in ("shift_light", "full_beam", "handbrake", "tc", "abs", "hazard", "battery_warn"))
    assert beamng.decode_packet(_packet(with_id=False))["id"] is None


def test_decode_light_bits():
    show = beamng.DL_SIGNAL_L | beamng.DL_SIGNAL_R | beamng.DL_HANDBRAKE | beamng.DL_FULLBEAM | beamng.DL_ABS
    d = beamng.decode_packet(_packet(show=show))
    assert d["signal_left"] and d["signal_right"] and d["hazard"] and d["handbrake"] and d["full_beam"]
    assert d["abs"] and not d["tc"] and not d["shift_light"] and not d["oil_warn"]
    d = beamng.decode_packet(_packet(show=beamng.DL_SIGNAL_L | beamng.DL_SHIFT | beamng.DL_TC | beamng.DL_BATTERY))
    assert d["signal_left"] and not d["hazard"] and d["shift_light"] and d["tc"] and d["battery_warn"]


def test_gears():
    assert [beamng.decode_packet(_packet(gear=g))["gear"] for g in (-1, 0, 1, 2, 7)] == ["R2", "R", "N", "1", "6"]


def test_short_packet_rejected():
    with pytest.raises(ValueError):
        beamng.decode_packet(b"\x00" * 50)


def test_lenient_json():
    text = '{\n  // comment\n  "a": [1, 2,],\n  /* block */ "b": "http://x//y",\n}'
    assert beamng.loads_lenient(text) == {"a": [1, 2], "b": "http://x//y"}


def test_parse_control():
    p = beamng.parse_control
    assert p("ctrl-alt p") == ["ctrl", "alt", "p"]
    assert p("shift-ctrl b") == ["ctrl", "shift", "b"]
    assert p("alt shift n") == ["shift", "alt", "n"]
    assert p("shift ctrl-alt space") == ["ctrl", "shift", "alt", "space"]
    assert p("numpad5") == ["num5"] and p("slash") == ["slash"] and p("escape") == ["esc"]
    assert p("lshift") == ["shift"] and p("tilde") == ["grave"] and p("*") == ["nummul"]
    assert p("xaxis") is None and p("button3") is None


KEYBOARD_JSON = """{
  "displayName":"Keyboard",
  "bindings":[
    {"control":"n", "action":"toggle_headlights"},
    {"control":"h", "action":"horn"},
    //{"control":"insert", "action":"cui_action_2" },
    {"control":"insert", "action":"recover_vehicle"},
    {"control":"ctrl-alt p", "action":"photomode"},
    {"control":"slash", "action":"toggle_hazard_signal"},
  ]
}"""


def test_read_bindings_with_user_diff(tmp_path):
    gdir, udir = tmp_path / "game", tmp_path / "user"
    (gdir / "settings" / "inputmaps").mkdir(parents=True)
    (udir / "settings" / "inputmaps").mkdir(parents=True)
    (gdir / "settings" / "inputmaps" / "keyboard.json").write_text(KEYBOARD_JSON, encoding="utf-8")
    diff = {"bindings": [{"control": "shift h", "action": "horn"}, {"control": "f6", "action": "toggleUi"}],
            "removed": [{"control": "slash", "action": "toggle_hazard_signal"}], "devicetype": "keyboard"}
    (udir / "settings" / "inputmaps" / "keyboard.diff").write_text(json.dumps(diff), encoding="utf-8")
    b = beamng.read_bindings(udir, gdir)
    assert b["toggle_headlights"].combo == ["n"]
    assert b["horn"].combo == ["shift", "h"]               # user binding wins over the stock one
    assert b["toggleUi"].combo == ["f6"]
    assert "toggle_hazard_signal" not in b
    assert b["photomode"].combo == ["ctrl", "alt", "p"] and "cui_action_2" not in b


def test_read_bindings_without_game_files(tmp_path):
    b = beamng.read_bindings(tmp_path / "nouser", tmp_path / "nogame")
    assert b["recover_vehicle"].combo == ["insert"] and b["photomode"].combo == ["ctrl", "alt", "p"]


def test_defaults_cover_actions_and_known_keys():
    from freedock.platform.keys import WIN_VK
    assert set(beamng.DEFAULT_KEYS) <= set(beamng.ACTIONS)
    assert all(k in WIN_VK for combo in beamng.DEFAULT_KEYS.values() for k in combo)
    assert all(v in WIN_VK for v in beamng._BNG_KEYS.values())


def _user(tmp_path, cloud: dict | None, local: dict | None):
    udir = tmp_path / "user"
    (udir / "settings" / "cloud").mkdir(parents=True)
    if cloud is not None:
        (udir / "settings" / "cloud" / "settings.json").write_text(json.dumps(cloud), encoding="utf-8")
    if local is not None:
        (udir / "settings" / "settings.json").write_text(json.dumps(local), encoding="utf-8")
    return udir


def test_outgauge_status_merge(tmp_path):
    gdir = tmp_path / "game"
    (gdir / "settings").mkdir(parents=True)
    (gdir / "settings" / "defaults.json").write_text(
        '// header\n{\n "protocols_outgauge_enabled": [ "cloud", false ],\n'
        ' "protocols_outgauge_port": [ "cloud", 4444 ],\n}', encoding="utf-8")
    udir = _user(tmp_path, {"autoClutch": False}, {"lastVersion": "0.40"})
    s = beamng.outgauge_status(udir, gdir)
    assert s["enabled"] is False and s["port"] == 4444 and s["ip"] == "127.0.0.1"
    assert s["settings_file"] == udir / "settings" / "cloud" / "settings.json"
    udir2 = _user(tmp_path / "b", {"protocols_outgauge_enabled": True, "protocols_outgauge_port": 5000},
                  {"outgaugePort": 6000})                         # deprecated local key still wins
    s = beamng.outgauge_status(udir2, gdir)
    assert s["enabled"] is True and s["port"] == 6000


def test_enable_outgauge_on_temp_copy(tmp_path):
    cloud0 = {"autoClutch": False, "telemetry": "enable", "outgaugeEnabled": False}
    udir = _user(tmp_path, cloud0, {"lastVersion": "0.40.4.0"})
    path = beamng.enable_outgauge(port=4567, udir=udir)
    assert path == udir / "settings" / "cloud" / "settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["protocols_outgauge_enabled"] is True and data["protocols_outgauge_port"] == 4567
    assert data["protocols_outgauge_address"] == "127.0.0.1"
    assert data["autoClutch"] is False and data["telemetry"] == "enable" and "outgaugeEnabled" not in data
    baks = list(path.parent.glob("settings.json.freedock-*.bak"))
    assert len(baks) == 1 and json.loads(baks[0].read_text(encoding="utf-8")) == cloud0
    local = json.loads((udir / "settings" / "settings.json").read_text(encoding="utf-8"))
    assert local == {"lastVersion": "0.40.4.0"}                   # untouched: no outgauge keys there
    s = beamng.outgauge_status(udir, tmp_path / "nogame")
    assert s["enabled"] and s["port"] == 4567


def test_enable_outgauge_overrides_local_and_creates_cloud(tmp_path):
    udir = _user(tmp_path, None, {"protocols_outgauge_enabled": False, "x": 1})
    beamng.enable_outgauge(udir=udir, backup=False)
    assert json.loads((udir / "settings" / "settings.json").read_text())["protocols_outgauge_enabled"] is True
    assert beamng.outgauge_status(udir, tmp_path / "nogame")["enabled"] is True
    assert not list((udir / "settings").rglob("*.bak"))


def test_enable_outgauge_refuses_broken_json(tmp_path):
    udir = _user(tmp_path, None, None)
    (udir / "settings" / "cloud" / "settings.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ValueError):
        beamng.enable_outgauge(udir=udir)
