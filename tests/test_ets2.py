import struct

from freedock.integrations import ets2

# Offsets written out literally (scs-sdk-plugin V.1.12.1) so a typo in ets2.OFF is caught here.


def _buf() -> bytearray:
    b = bytearray(ets2.MAP_SIZE)
    struct.pack_into("<?", b, 0, True)                  # sdkActive
    struct.pack_into("<?", b, 4, False)                 # paused
    struct.pack_into("<Q", b, 24, 123456)               # renderTime
    struct.pack_into("<I", b, 40, 12)                   # plugin revision
    struct.pack_into("<I", b, 52, 2)                    # game = ATS
    struct.pack_into("<I", b, 64, 1440 + 14 * 60 + 5)   # Tue 14:05
    struct.pack_into("<I", b, 76, 4)                    # retarderStepCount
    struct.pack_into("<I", b, 88, 1440 + 18 * 60)       # time_abs_delivery: Tue 18:00
    struct.pack_into("<I", b, 100, 352)                 # plannedDistanceKm
    struct.pack_into("<I", b, 108, 2)                   # retarderBrake
    struct.pack_into("<i", b, 500, 365)                 # restStop
    struct.pack_into("<i", b, 508, -1)                  # gearDashboard -> R1
    struct.pack_into("<f", b, 704, 900.0)               # fuelCapacity
    struct.pack_into("<f", b, 740, 2500.0)              # engineRpmMax
    struct.pack_into("<f", b, 948, 25.0)                # speed m/s
    struct.pack_into("<f", b, 952, 1200.0)              # rpm
    struct.pack_into("<f", b, 988, 22.5)                # cruise m/s
    struct.pack_into("<f", b, 1000, 450.0)              # fuel
    struct.pack_into("<f", b, 1008, 1500.0)             # fuel range km
    struct.pack_into("<f", b, 1012, 55.0)               # adblue
    struct.pack_into("<f", b, 1044, 0.07)               # wearCabin (max wear)
    struct.pack_into("<f", b, 1052, 0.02)               # wearWheels
    struct.pack_into("<f", b, 1068, 25.0)               # speedLimit m/s
    struct.pack_into("<f", b, 1468, 0.015)              # job cargoDamage
    for off in (1566, 1575, 1576, 1578, 1583, 1585, 1589, 1608, 4300, 6080):
        b[off] = 1          # parkBrake electric engine blinkerL lowBeam beacon cruise diffLock onJob attached
    for off, s in ((2364, "Kenworth"), (2492, "W900"), (2620, "Lumber"), (2748, "Reno"), (3004, "Sacramento")):
        b[off:off + len(s)] = s.encode()
    struct.pack_into("<Q", b, 4000, 18_450)             # jobIncome
    return b


def test_decode_synthetic_buffer():
    d = ets2.decode(bytes(_buf()))
    assert d["connected"] is True and d["paused"] is False and d["game"] == "ats" and d["plugin_rev"] == 12
    assert d["truck"] == "Kenworth W900"
    assert abs(d["speed_kmh"] - 90.0) < 1e-3 and abs(d["cruise_kmh"] - 81.0) < 1e-3 and d["cruise_on"]
    assert d["rpm"] == 1200.0 and d["max_rpm"] == 2500.0 and d["gear"] == "R1"
    assert d["fuel_l"] == 450.0 and d["fuel_cap_l"] == 900.0 and d["fuel_range_km"] == 1500.0
    assert d["adblue"] == 55.0 and abs(d["speed_limit_kmh"] - 90.0) < 1e-3
    assert d["parking_brake"] and d["electric_on"] and d["engine_on"] and d["blinker_left"]
    assert not d["blinker_right"] and d["lights_low"] and not d["lights_high"] and d["beacon"]
    assert not d["hazard"] and d["diff_lock"] and not d["lift_axle"] and d["trailer_attached"]
    assert d["retarder"] == 2 and d["retarder_max"] == 4
    assert abs(d["truck_damage"] - 0.07) < 1e-6 and abs(d["cargo_damage"] - 0.015) < 1e-6
    assert d["job"] == {"cargo": "Lumber", "source_city": "Sacramento", "source_company": "",
                        "dest_city": "Reno", "dest_company": "", "income": 18450, "planned_km": 352,
                        "deadline_min": 235}
    assert d["rest_stop_min"] == 365 and d["game_clock"] == "Tue 14:05"
    # 0.4: no route advisor / heading any more - they kept the keys redrawing nonstop
    assert not {"nav_distance_km", "nav_time_min", "eta_clock", "trip_progress", "heading_deg"} & set(d)


def test_money_fits_a_key():
    from freedock.widgets import fmt_money
    assert fmt_money(8450, "€") == "€8,450" and fmt_money(18_450, "€") == "€18.4k"
    assert fmt_money(1_234_567, "$") == "$1.2M" and fmt_money(0, "€") == "€0"


def test_no_gps_metrics_left():
    from freedock.presets import ETS2_GPS_METRICS, build_default_profile
    from freedock.widgets import ETS2_METRICS
    assert not ETS2_GPS_METRICS & {m for m, _ in ETS2_METRICS}
    prof = build_default_profile("pt")
    for page in prof.pages:
        for slot in [*page.keys, *page.zones]:
            assert (slot.widget or {}).get("metric") not in ETS2_GPS_METRICS, page.id
    assert "ets2-nogps" in prof.seen_presets


def test_saved_profile_loses_the_gps_once():
    from freedock.presets import add_new_presets, build_default_profile
    from freedock.profile import Profile, Slot
    prof = build_default_profile("pt")
    old = Profile.from_dict(prof.to_dict())          # a 0.3 profile with the route advisor and compass
    old.seen_presets.remove("ets2-nogps")
    old.page("ets2").style["change"] = "slide"
    old.page("ets2").zones[2] = Slot(widget={"type": "ets2", "metric": "trip"}, action={"type": "ets2_binding",
                                                                                       "binding": "navmap"})
    trip = old.page("ets2_trip")
    trip.keys[2] = Slot(widget={"type": "ets2", "metric": "trip"})
    trip.keys[3] = Slot(widget={"type": "ets2", "metric": "compass"})
    old.page("daily").keys[0] = Slot(label="Mine", widget={"type": "ets2", "metric": "nav"})
    assert add_new_presets(old, "pt") is True
    assert old.page("ets2").zones[2].widget == {"type": "ets2", "metric": "diff_lock"}
    assert old.page("ets2").zones[2].action == {"type": "ets2_binding", "binding": "diflock"}
    assert old.page("ets2").style["change"] == "fade"
    assert [s.widget["metric"] for s in trip.keys[2:4]] == ["cargo", "income"]
    assert old.page("daily").keys[0].label == "Mine" and old.page("daily").keys[0].widget["metric"] == "job"
    assert add_new_presets(old, "pt") is False      # once


def test_decode_inactive_or_stale():
    b = _buf()
    assert ets2.decode(bytes(b), stale=True)["connected"] is False
    b[0] = 0
    b[4300] = 0
    d = ets2.decode(bytes(b))
    assert d["connected"] is False and d["job"] is None


def test_gear_and_clock_format():
    assert [ets2.fmt_gear(g) for g in (-2, -1, 0, 1, 12)] == ["R2", "R1", "N", "1", "12"]
    assert ets2.fmt_game_clock(0) == "Mon 00:00" and ets2.fmt_game_clock(6 * 1440 + 23 * 60 + 59) == "Sun 23:59"


def test_parse_mix_expressions():
    p = ets2.parse_mix
    assert p("keyboard.l?0 || short_press(joy2.b3?0) | semantical.light?0") == ["l"]
    assert p("keyboard.lbracket?0 | semantical.lblinker?0") == ["bracketleft"]
    assert p("modifier(ctrl_only, keyboard.f9?0)") == ["ctrl", "f9"]
    assert p("modifier(ctr_shf, keyboard.f12?0)") == ["ctrl", "shift", "f12"]
    assert p("modifier(no_modifier, keyboard.nummultiply?0)") == ["nummul"]
    assert p("keyboard.lalt?0 & keyboard.d?0") == ["alt", "d"]
    assert p("keyboard.key1?0 || long_press(joy2.pov1_up?0)") == ["1"]
    assert p("unbound?0 | joy.b22?0 | semantical.cruiectrlinc?0") is None
    assert p("modifier(cstm_mod_1?0 & (! cstm_mod_2?0), joy2.b11?0) | keyboard.equal?0") == ["equal"]
    assert p("mouse.button_left?0") is None and p("") is None


SAMPLE = """SiiNunit
{
input_config : _nameless.262.20bc.d540 {
 config_lines: 6
 config_lines[0]: "device keyboard `sys.keyboard`"
 config_lines[1]: "mix engine `keyboard.e?0 || long_press(joy2.pov1_right?0) | semantical.engine?0`"
 config_lines[2]: "mix beacon `modifier(shift_only, keyboard.o?0) | semantical.beacon?0`"
 config_lines[3]: "mix horn `joy2.b9?0 | semantical.horn?0`"
 config_lines[4]: "mix engineelect `keyboard.numenter?0`"
 config_lines[5]: "mix steering `dsteerleft - dsteerright`"
}
}
"""


def test_read_bindings_merges_over_defaults(tmp_path):
    f = tmp_path / "controls.sii"
    f.write_text(SAMPLE.replace("\n", "\r\n"), encoding="utf-8")
    b = ets2.read_bindings(f)
    assert b["engine"].combo == ["e"]
    assert b["beacon"].combo == ["shift", "o"]
    assert b["engineelect"].combo == ["numenter"]
    assert "horn" not in b                       # present in the file, keyboard-unbound -> stays unbound
    assert b["airhorn"].combo == ["n"]           # absent from the file -> stock default
    assert "steering" not in b
    assert ets2.read_bindings(tmp_path / "missing.sii")["engine"].combo == ["e"]
    assert ets2.read_bindings(None)["menu"].combo == ["esc"]


def test_defaults_cover_actions_and_known_keys():
    from freedock.platform.keys import WIN_VK
    assert set(ets2.DEFAULT_KEYS) <= set(ets2.ACTIONS)
    assert all(k in WIN_VK for combo in ets2.DEFAULT_KEYS.values() for k in combo)
    assert all(v in WIN_VK for v in ets2._SCS_KEYS.values())


def test_plugin_installed(tmp_path):
    assert not ets2.plugin_installed(tmp_path)
    plugins = tmp_path / "bin" / "win_x64" / "plugins"
    plugins.mkdir(parents=True)
    assert not ets2.plugin_installed(tmp_path)
    (plugins / "scs-telemetry.dll").write_bytes(b"MZ")
    assert ets2.plugin_installed(tmp_path)
