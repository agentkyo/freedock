"""Games of 0.5: Motor Town telemetry, AC EVO shared memory, the deck pages and their keys."""
import struct

from freedock import presets
from freedock.integrations import motortown as mt
from freedock.integrations.assetto import AssettoTelemetry
from freedock.integrations.scenes import THEMES, theme_for
from freedock.platform.keys import WIN_VK, parse_combo
from freedock.profile import Profile


def mt_packet(v11: bool = True, *, rpm=1800.0, gear=4, kmh=72.0, flags=0x02 | 0x08 | 0x40, signal=1,
              fuel=0.5, limit=80.0, money=123456) -> bytes:
    size = mt.V11_SIZE if v11 else mt.V1_SIZE
    b = bytearray(size)
    b[0:4] = b"MTTM"
    b[4], b[5] = 1, 1 if v11 else 0
    struct.pack_into("<H", b, 6, size - 8)
    struct.pack_into("<ffiif", b, 108, rpm, 2600.0, gear, 12, kmh)
    struct.pack_into("<I", b, 145, flags)
    struct.pack_into("<f", b, 149, fuel)
    if v11:
        b[158] = signal
        struct.pack_into("<f", b, 190, 70.0)
        struct.pack_into("<ff", b, 202, 200.0, 400.0)
        struct.pack_into("<f", b, 230, limit)
        struct.pack_into("<q", b, 284, money)
        b[753:753 + 9] = b"Scania R\x00"
    return bytes(b)


def test_motortown_packet_v11():
    d = mt.decode_packet(mt_packet())
    assert d["connected"] and d["in_vehicle"] and d["lights"] and d["cruise"]
    assert d["gear"] == "4" and round(d["speed_kmh"]) == 72 and d["max_rpm"] == 2600.0
    assert d["signal"] == "left" and d["signal_left"] and not d["signal_right"]
    assert d["road_limit_kmh"] == 80.0 and d["money"] == 123456 and d["vehicle"] == "Scania R"
    assert d["fuel_l"] == 200.0


def test_motortown_packet_v1_reverse_and_garbage():
    d = mt.decode_packet(mt_packet(v11=False, gear=-1, flags=0x02 | 0x04))
    assert d["gear"] == "R" and d["reverse"] and d["signal"] is None
    assert mt.decode_packet(b"MTTM" + bytes(10)) is None
    assert mt.decode_packet(b"XXXX" + mt_packet()[4:]) is None
    assert mt.decode_packet(mt_packet()[:-1]) is None          # size field disagrees


def test_motortown_strip_blinks_the_lamp():
    d = mt.decode_packet(mt_packet(signal=3))                   # hazard
    on = mt.strip_signals(d, 0.0)
    off = mt.strip_signals(d, mt.BLINK_S)
    assert on["left"] and on["right"] and not off["left"] and not off["right"]


def test_acevo_shared_memory_decode():
    ph = bytearray(256)
    struct.pack_into("<ifffii", ph, 0, 7, 0.8, 0.0, 41.5, 4, 6400)      # gear 4 -> 3rd
    struct.pack_into("<f", ph, 28, 151.0)
    struct.pack_into("<i", ph, 248, 1)
    gf = bytearray(96)
    struct.pack_into("<ii", gf, 0, 7, 2)                                   # live
    struct.pack_into("<f", gf, 72, 0.8)
    d = AssettoTelemetry()._decode_evo(bytes(ph), bytes(gf))
    assert d["connected"] and d["game"] == "acevo"
    assert d["gear"] == "3" and d["rpm"] == 6400 and d["max_rpm"] == 8000 and d["pit_limiter"]
    assert round(d["speed"]) == 151 and d["fuel"] == 41.5


def test_new_pages_build_with_valid_keys_and_rules():
    import freedock.presets_games as pg
    for pid in ("acevo", "motortown", "slrr", "nsgp", "nfsheat", "ccd2", "fc26"):
        page = presets.NEW_PAGES[pid]("pt")
        assert page.id == pid and len(page.keys) == 10 and len(page.zones) == 4 and len(page.knobs) == 4
        for slot in [*page.keys, *page.zones]:
            for a in (slot.action, slot.long_action):
                if a and a["type"] == "hotkey":
                    assert all(k in WIN_VK for k in parse_combo(a["keys"])), (pid, a)
        for k in page.knobs:
            for a in (k.left, k.right, k.press):
                if a and a["type"] == "hotkey":
                    assert all(k2 in WIN_VK for k2 in parse_combo(a["keys"])), (pid, a)
    rules = {r["process"].lower(): r["page"] for r in presets.NEW_RULES}
    assert rules["motortown-win64-shipping.exe"] == "motortown" and rules["nsgp.exe"] == "nsgp"
    assert rules["fc26.exe"] == "fc26"
    assert pg.NEW_PAGES is presets.NEW_PAGES


def test_existing_profile_gets_the_new_pages_once():
    prof = presets.build_default_profile("pt")
    prof.pages = [p for p in prof.pages if p.id not in ("motortown", "slrr")]
    prof.seen_presets = [s for s in prof.seen_presets if s not in ("motortown", "slrr")]
    assert presets.add_new_presets(prof, "pt")
    assert prof.page("motortown") and prof.page("slrr")
    assert any(r["page"] == "motortown" for r in prof.auto_switch)
    assert not presets.add_new_presets(prof, "pt")
    assert isinstance(prof, Profile)


def test_light_themes_for_the_new_games():
    for exe, tid in (("motortown-win64-shipping.exe", "motortown"), ("assettocorsaevo.exe", "acevo"),
                     ("nsgp.exe", "nsgp"), ("streetlegal_redline.exe", "slrr"), ("needforspeedheat.exe", "nfsheat"),
                     ("citycardriving2-win64-shipping.exe", "ccd2")):
        assert theme_for({exe}).id == tid
        assert THEMES[tid].page == tid
