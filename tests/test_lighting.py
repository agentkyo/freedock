"""Pure logic of the lighting integration. Nothing here touches a real strip, HID device,
socket to the Ambibox tray or the Chroma SDK: every transport is a fake."""
import pytest

from freedock.integrations import lighting as L


# --------------------------------------------------------------------------- vendored strip protocol

def test_section_packet_matches_ambibox_capture():
    # Same vector as ambibox-led-ambilight's test suite (vendor app capture), built by hand:
    # LEDs 1-65 blue, the rest off, message id 0x0e.
    payload = bytes((1, 0, 0, 255, 65, 66, 0, 0, 0, L.TO_END))
    assert L.rb_packet(L.CMD_SECTION, payload, 0x0E) == bytes.fromhex("5242100e86010000ff4142000000feb9")


def test_brightness_and_fill_packets():
    assert L.brightness_packet(200, 1) == bytes.fromhex("5242070187c8eb")
    assert L.brightness_packet(0, 1)[5] == 5
    assert L.fill_packet((300, -1, 7), 2) == L.rb_packet(L.CMD_SECTION, bytes((1, 255, 0, 7, 254)), 2)


def test_sync_packet_for_71_leds_spans_six_reports():
    packet = L.sync_packet([(1, 2, 3)] * 71, 5)
    assert len(packet) == 6 + 71 * 5 + 1 == 362  # length field counts the whole packet
    assert packet[:6] == b"SC" + (362).to_bytes(2, "big") + bytes((5, 0x80))
    assert packet[6:11] == bytes((1, 1, 2, 3, 1))
    assert packet[-1] == sum(packet[:-1]) & 0xFF
    reports = L.to_reports(packet)
    assert len(reports) == 6 and all(len(r) == 64 for r in reports)


def test_frames_never_exceed_the_strip():
    assert L.fit_frame([(9, 9, 9)] * 150, 71) == [(9, 9, 9)] * 71
    assert L.fit_frame([(9, 9, 9)], 3) == [(9, 9, 9), (0, 0, 0), (0, 0, 0)]
    with pytest.raises(ValueError):
        L.sync_packet([(0, 0, 0)] * 254, 1)


def test_effect_names():
    assert L.effect_index("rainbow") == 0 and L.effect_index("Marquee") == 6 and L.effect_index("3") == 3
    assert L.effect_packet(2, 9) == L.rb_packet(L.CMD_EFFECT, bytes((2, 2)), 9)
    for bad in (7, -1, "disco", True):
        with pytest.raises(ValueError):
            L.effect_index(bad)


# --------------------------------------------------------------------------- bar mapping

RGB = ((0, 255, 0), (255, 255, 0), (255, 0, 0))


def test_bar_lights_a_proportional_run_with_gradient_colors():
    cells = L.bar_colors(0.5, 10, RGB)
    assert cells[:5] == [L.gradient_at(RGB, i / 9) for i in range(5)]
    assert cells[5:] == [(0, 0, 0)] * 5
    assert L.bar_colors(1.0, 10, RGB)[-1] == (255, 0, 0)
    assert L.bar_colors(0, 10, RGB) == [(0, 0, 0)] * 10
    assert L.bar_colors(7, 4, [(1, 2, 3)]) == [(1, 2, 3)] * 4  # clamped, solid color
    assert L.bar_colors(0.5, 0, RGB) == []


def test_mirrored_bar_rises_from_both_ends():
    cells = L.bar_colors(0.5, 71, [(255, 0, 0)], mirror=True)
    assert len(cells) == 71
    assert cells == cells[::-1]
    assert cells[0] == cells[-1] == (255, 0, 0) and cells[35] == (0, 0, 0)


def test_keyboard_bar_uses_f_row_or_number_row():
    grid = L.keyboard_bar(0.5, [(255, 0, 0)])
    assert len(grid) == L.KB_ROWS and all(len(r) == L.KB_COLS for r in grid)
    red = L.bgr((255, 0, 0))
    assert [grid[0][c] for c in range(3, 15)] == [red] * 6 + [0] * 6  # F1-F6 lit
    assert sum(v != 0 for row in grid for v in row) == 6
    grid = L.keyboard_bar(1.0, [(0, 0, 255)], row="number")
    assert [grid[1][c] for c in range(2, 12)] == [L.bgr((0, 0, 255))] * 10
    assert grid[0][3] == 0


def test_chroma_bodies():
    assert L.bgr((0x11, 0x22, 0x33)) == 0x332211
    assert L.static_body((255, 0, 0)) == {"effect": "CHROMA_STATIC", "param": {"color": 255}}
    assert L.custom_body(L.keyboard_grid())["effect"] == "CHROMA_CUSTOM"
    assert L.NONE_BODY == {"effect": "CHROMA_NONE"}


# --------------------------------------------------------------------------- strip client, driven by hand

class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeUdp:
    def __init__(self, status=None):
        self.sent = []
        self.status = status if status is not None else {"mode": "ambilight", "connected": True, "count": 71}

    def send(self, command):
        self.sent.append(command)

    def request(self, command, timeout=0.25):
        assert command == {"cmd": "status"}
        return self.status

    def close(self):
        pass


class FakeDirect:
    instances = []

    def __init__(self, led_count, brightness=255):
        self.led_count = led_count
        self.log = []
        self.is_open = False
        FakeDirect.instances.append(self)

    def open(self):
        self.is_open = True
        return True

    def fill(self, color):
        self.log.append(("fill", tuple(color)))
        return True

    def show(self, colors):
        self.log.append(("show", len(L.fit_frame(colors, self.led_count))))
        return True

    def effect(self, index):
        self.log.append(("effect", index))
        return True

    def set_brightness(self, value):
        self.log.append(("brightness", value))
        return True

    def close(self):
        self.is_open = False
        self.log.append(("close",))


def make_strip(tray=True, status=None, count=71):
    clock = Clock()
    udp = FakeUdp(status) if tray else FakeUdp()
    strip = L.AmbiboxStrip(count, transport=udp, tray_probe=lambda: tray, direct=FakeDirect,
                           present=lambda: True, clock=clock)
    strip._wake = lambda: None  # no thread: tests call _tick themselves
    return strip, udp, clock


def drain(strip, clock, steps=20):
    for _ in range(steps):
        strip._tick(clock())


def test_tray_mode_sends_udp_commands_and_clamps_frames_to_tray_count():
    strip, udp, clock = make_strip(status={"count": 60})
    strip.color((255, 0, 0))
    drain(strip, clock)
    assert strip.mode == "tray" and strip.count() == 60
    assert udp.sent == [{"cmd": "color", "rgb": [255, 0, 0]}]
    clock.t += 1
    strip.leds([(1, 2, 3)] * 71)
    drain(strip, clock)
    assert udp.sent[-1]["cmd"] == "leds" and len(udp.sent[-1]["colors"]) == 60
    assert udp.sent[-1]["ttl_ms"] == L.LEDS_TTL_MS


def test_frames_are_coalesced_and_rate_limited():
    strip, udp, clock = make_strip()
    for i in range(10):
        strip.leds([(i, 0, 0)] * 5)
    drain(strip, clock)
    assert [c["colors"][0][0] for c in udp.sent] == [9]  # only the newest went out
    strip.leds([(20, 0, 0)] * 5)
    clock.t += 0.01  # sooner than 1/30 s
    drain(strip, clock)
    assert len(udp.sent) == 1
    clock.t += 1 / 30
    drain(strip, clock)
    assert udp.sent[-1]["colors"][0][0] == 20


def test_still_bar_is_kept_alive_until_restore():
    strip, udp, clock = make_strip()
    strip.bar(0.5, [(255, 0, 0)])
    drain(strip, clock)
    assert len(udp.sent) == 1
    clock.t += L.KEEPALIVE_S + 0.01
    drain(strip, clock)
    assert len(udp.sent) == 2 and udp.sent[1] == udp.sent[0]
    strip.restore()
    drain(strip, clock)
    clock.t += 5
    drain(strip, clock)
    assert udp.sent[-1] == {"cmd": "restore"}
    assert sum(c["cmd"] == "leds" for c in udp.sent) == 2


def test_discrete_commands_keep_order_and_drop_stale_frames():
    strip, udp, clock = make_strip()
    strip.color((1, 1, 1))
    strip.flash((255, 255, 255), times=2)
    strip.effect("meteor")
    drain(strip, clock)
    assert [c["cmd"] for c in udp.sent] == ["flash", "effect"]
    assert udp.sent[0] == {"cmd": "flash", "rgb": [255, 255, 255], "times": 2, "on_ms": 180, "off_ms": 120}
    assert udp.sent[1] == {"cmd": "effect", "index": 3}


def test_old_tray_without_control_channel_is_left_alone():
    strip, udp, clock = make_strip()
    udp.request = lambda command, timeout=0.25: None
    FakeDirect.instances.clear()
    strip.color((255, 0, 0))
    drain(strip, clock)
    assert strip.status() == "tray-busy"
    assert udp.sent == [] and FakeDirect.instances == []


def test_direct_mode_without_the_tray_respects_led_count():
    FakeDirect.instances.clear()
    strip, udp, clock = make_strip(tray=False, count=30)
    strip.leds([(5, 5, 5)] * 100)
    drain(strip, clock)
    assert strip.mode == "direct" and udp.sent == []
    dev = FakeDirect.instances[-1]
    assert dev.led_count == 30 and ("show", 30) in dev.log
    strip.restore()
    drain(strip, clock)
    assert dev.log[-2:] == [("fill", (0, 0, 0)), ("close",)]


def test_direct_color_ttl_switches_off():
    FakeDirect.instances.clear()
    strip, _, clock = make_strip(tray=False)
    strip.color((0, 0, 255), ttl_ms=500)
    drain(strip, clock)
    dev = FakeDirect.instances[-1]
    clock.t += 0.6
    drain(strip, clock)
    assert dev.log[-1] == ("close",)


def test_absent_strip_is_a_noop():
    strip, udp, clock = make_strip(tray=False)
    strip._present = lambda: False
    strip._direct_factory = lambda n: pytest.fail("must not open a device")

    class Missing(FakeDirect):
        def open(self):
            return False
    strip._direct_factory = Missing
    strip.color((1, 2, 3))
    drain(strip, clock)
    assert strip.mode == "absent" and udp.sent == []


# --------------------------------------------------------------------------- Chroma client with a fake HTTP layer

class FakeHttp:
    def __init__(self, up=True):
        self.up = up
        self.calls = []

    def __call__(self, method, url, body=None, timeout=2.0):
        self.calls.append((method, url, body))
        if not self.up:
            raise OSError("connection refused")
        if method == "POST":
            return {"sessionid": 1234, "uri": "http://localhost:55555/chromasdk"}
        return {"result": 0}


def make_chroma(up=True):
    clock = Clock()
    http = FakeHttp(up)
    chroma = L.ChromaClient(http=http, clock=clock)
    chroma._wake = lambda: None
    return chroma, http, clock


def run_chroma(chroma, clock, seconds, step=0.01):
    end = clock.t + seconds
    while clock.t < end:
        chroma._tick(clock())
        clock.t += step


def test_chroma_init_body_and_static_to_every_device():
    chroma, http, clock = make_chroma()
    chroma.static((255, 0, 0))
    run_chroma(chroma, clock, 1.2)
    method, url, body = http.calls[0]
    assert (method, url) == ("POST", L.CHROMA_URL)
    assert body["title"] == "FreeDock" and body["category"] == "application"
    assert set(body["device_supported"]) == set(L.CHROMA_DEVICES)
    puts = [(u.rsplit("/", 1)[1], b) for m, u, b in http.calls if m == "PUT" and not u.endswith("heartbeat")]
    assert sorted(d for d, _ in puts) == sorted(L.CHROMA_DEVICES)
    assert all(b == {"effect": "CHROMA_STATIC", "param": {"color": 255}} for _, b in puts)
    assert chroma.status() == "connected"


def test_chroma_waits_for_the_session_to_settle():
    chroma, http, clock = make_chroma()
    chroma.keyboard(L.keyboard_grid((1, 2, 3)))
    run_chroma(chroma, clock, L.INIT_SETTLE_S - 0.1)
    assert [m for m, _, _ in http.calls] == ["POST"]
    run_chroma(chroma, clock, 0.2)
    assert http.calls[-1][1].endswith("/keyboard") and http.calls[-1][2]["effect"] == "CHROMA_CUSTOM"


def test_chroma_rate_limit_and_coalescing():
    chroma, http, clock = make_chroma()
    chroma.keyboard(L.keyboard_grid())
    run_chroma(chroma, clock, 1.2)
    sent_before = len(http.calls)
    for i in range(50):  # one second of 50 fps requests
        chroma.keyboard(L.keyboard_bar(i / 50, [(255, 0, 0)]))
        run_chroma(chroma, clock, 0.02, step=0.02)
    keyboard_puts = [c for c in http.calls[sent_before:] if c[1].endswith("/keyboard")]
    assert len(keyboard_puts) <= 21
    run_chroma(chroma, clock, 0.2)
    assert http.calls[-1][2] == L.custom_body(L.keyboard_bar(49 / 50, [(255, 0, 0)]))


def test_chroma_heartbeat_and_release():
    chroma, http, clock = make_chroma()
    chroma.static((0, 255, 0), devices=("keyboard",))
    run_chroma(chroma, clock, 25, step=0.5)
    beats = [c for c in http.calls if c[1].endswith("/heartbeat")]
    assert len(beats) >= 2
    chroma.release()
    run_chroma(chroma, clock, 0.1)
    assert http.calls[-1] == ("DELETE", "http://localhost:55555/chromasdk", None)
    assert chroma.status() == "absent"


def test_chroma_absent_logs_once_and_retries_every_30s(caplog):
    chroma, http, clock = make_chroma(up=False)
    chroma.static((1, 1, 1))
    with caplog.at_level("INFO", logger=L.__name__):
        run_chroma(chroma, clock, 65, step=0.5)
    assert [m for m, _, _ in http.calls] == ["POST"] * 3  # t=0, 30, 60
    assert sum("not answering" in r.message for r in caplog.records) == 1
    http.up = True
    run_chroma(chroma, clock, 30, step=0.5)
    assert chroma.status() == "connected"


def test_chroma_flash_then_back_to_what_was_shown():
    chroma, http, clock = make_chroma()
    chroma.static((0, 0, 255), devices=("keyboard",))
    run_chroma(chroma, clock, 1.2)
    chroma.flash((255, 0, 0), times=2, on_ms=100, off_ms=100, devices=("keyboard",))
    run_chroma(chroma, clock, 1.0)
    bodies = [b for m, u, b in http.calls if u.endswith("/keyboard")]
    assert bodies[-5:] == [L.static_body((255, 0, 0)), L.NONE_BODY] * 2 + [L.static_body((0, 0, 255))]


def test_chroma_color_ttl_releases():
    chroma, http, clock = make_chroma()
    chroma.static((9, 9, 9), devices=("mouse",), ttl_ms=2000)
    run_chroma(chroma, clock, 2.5)
    assert http.calls[-1][0] == "DELETE"


# --------------------------------------------------------------------------- facade

class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a, k))

    def status(self):
        return "tray"

    def count(self):
        return 71


def test_facade_routes_targets_and_never_raises():
    strip, chroma = Recorder(), Recorder()
    lights = L.Lights(strip=strip, chroma=chroma)
    lights.color((300, 0, 0), target="strip", ttl_ms=500)
    assert strip.calls == [("color", ((255, 0, 0), 500), {})] and chroma.calls == []
    lights.bar(0.25, target="chroma", kb_row="number")
    assert chroma.calls[-1][0] == "keyboard" and len(chroma.calls[-1][1][0]) == 6
    lights.flash((1, 2, 3))
    assert strip.calls[-1][0] == "flash" and chroma.calls[-1][0] == "flash"
    lights.restore("chroma")
    assert chroma.calls[-1][0] == "release" and strip.calls[-1][0] == "flash"
    lights.color((1, 1, 1), target="keyboard")  # unknown target: logged, ignored
    lights.configure(strip_enabled=False, chroma_enabled=False)
    lights.color((1, 1, 1))
    assert lights.status() == {"strip": "disabled", "strip_count": 71, "chroma": "disabled"}


def test_facade_effect_by_name_reaches_the_strip_only():
    strip, chroma = Recorder(), Recorder()
    lights = L.Lights(strip=strip, chroma=chroma)
    lights.effect("rainbow")
    assert strip.calls == [("effect", ("rainbow",), {})] and chroma.calls == []
