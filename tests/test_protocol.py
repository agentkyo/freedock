from freedock.device import protocol
from freedock.device.layout import N4, EventType, Kind


def test_command_framing():
    r = protocol.brightness(55)
    assert len(r) == protocol.REPORT_SIZE == 1025
    assert r[:10] == b"\x00CRT\x00\x00LIG\x00"
    assert r[11] == 55
    assert set(r[12:]) == {0}


def test_brightness_is_clamped():
    assert protocol.brightness(250)[11] == 100
    assert protocol.brightness(-3)[11] == 0


def test_image_is_header_plus_padded_chunks():
    jpeg = bytes(range(256)) * 9            # 2304 bytes -> 3 data reports
    reports = protocol.image(11, jpeg)
    assert len(reports) == 4
    header = reports[0]
    assert header[:9] == b"\x00CRT\x00\x00BAT"
    assert int.from_bytes(header[9:13], "big") == len(jpeg)
    assert header[13] == 11
    assert all(len(r) == 1025 and r[0] == 0 for r in reports)
    assert b"".join(r[1:] for r in reports[1:])[:len(jpeg)] == jpeg


def test_image_size_limit():
    try:
        protocol.image(1, b"x" * (protocol.MAX_IMAGE_BYTES + 1))
    except ValueError:
        return
    raise AssertionError("oversized image accepted")


def test_background_header_matches_known_capture():
    # frame captured from the vendor app on an N4 Pro (see docs/PROTOCOL.md)
    h = protocol.background_header(22_517, 1, 1, 798, 478, (1, 0))
    assert h[:25] == b"\x00CRT\x00\x00BGPIC\x00\x00\x57\xf5\x00\x01\x00\x01\x03\x1e\x01\xde\x01\x00"


def test_parse_input():
    report = bytes([0x41, 0x43, 0x4B, 0, 0, 0x4F, 0x4B, 0, 0, 0x03, 0x01]) + bytes(501)
    assert protocol.parse_input(report) == (3, 1)
    assert protocol.parse_input(bytes(512)) is None
    assert protocol.parse_input(b"\x00" * 5) is None


def test_layout_decode():
    assert N4.decode(1, 1).kind == Kind.KEY and N4.decode(1, 1).index == 0
    assert N4.decode(10, 0).type == EventType.UP
    z = N4.decode(0x42, 0)
    assert z.kind == Kind.ZONE and z.index == 2 and z.type == EventType.TAP
    k = N4.decode(0x51, 0)
    assert k.kind == Kind.KNOB and k.index == 1 and k.value == 1
    assert N4.decode(0xA0, 0).value == -1
    assert N4.decode(0x36, 0).type == EventType.PUSH and N4.decode(0x36, 0).index == 3
    assert N4.decode(0x38, 0).kind == Kind.SWIPE and N4.decode(0x38, 0).value == -1
    assert N4.decode(0x99, 0) is None


def test_image_ids_cover_firmware_slots():
    assert sorted(N4.key_image_ids + N4.zone_image_ids) == list(range(1, 5)) + list(range(6, 16))
