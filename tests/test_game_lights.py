"""Game lighting: scenes (pure) and the director that sends them."""
from freedock.integrations import scenes
from freedock.integrations.cs2 import Cs2State, demo_payloads
from freedock.integrations.game_lights import GameLights
from freedock.integrations.lighting import Lights
from freedock.integrations.scenes import BLACK, Effect, Frame, hexc


def test_strip_bar_rises_from_the_ends_and_drains_to_the_middle():
    n = 20
    up = scenes.strip_bar(n, 0.3, ((255, 0, 0),))
    assert up[0] != BLACK and up[-1] != BLACK and up[n // 2] == BLACK
    down = scenes.strip_bar(n, 0.3, ((255, 0, 0),), inward=True)
    assert down[0] == BLACK and down[-1] == BLACK and down[n // 2] != BLACK
    assert all(c == BLACK for c in scenes.strip_bar(n, 0.0, ((255, 0, 0),), inward=True))
    assert len(scenes.strip_bar(71, 1.0, ((1, 2, 3),), mirror=False)) == 71


def test_bomb_beeps_speed_up():
    def blinks(lo, hi):
        prev, count = 0.0, 0
        for i in range(int(lo * 1000), int(hi * 1000)):        # each blink decays, then jumps back up
            b = scenes.bomb_beep(40.0 - i / 1000)
            count += b > prev
            prev = b
        return count
    assert 4 <= blinks(0, 5) <= 6 and blinks(34, 39) > 4 * blinks(0, 5)


def test_keyboard_map_and_groups():
    assert scenes.KEYS["w"] == (2, 3) and scenes.KEYS["a"] == (3, 2) and scenes.KEYS["f1"] == (0, 3)
    assert scenes.keys_of("wasd") == [(2, 3), (3, 2), (3, 3), (3, 4)]
    assert scenes.keys_of(["space", "qe"]) == [(5, 7), (2, 2), (2, 4)]


def test_foreground_game_picks_the_theme():
    assert scenes.theme_for({"explorer.exe"}) is None
    assert scenes.theme_for({"cs2.exe"}).id == "cs2"
    assert scenes.theme_for({"cs2.exe", "eurotrucks2.exe"}, "eurotrucks2.exe").id == "ets2"
    assert scenes.theme_for({"Beat Saber.exe".lower()}).id == "beatsaber"


def test_every_theme_renders_a_full_frame():
    for theme in scenes.THEMES.values():
        f = scenes.render_theme(theme, 3.3, 71)
        assert len(f.keyboard) == 6 and all(len(r) == 22 for r in f.keyboard) and len(f.strip) == 71
        assert all(0 <= v <= 255 for row in f.keyboard for c in row for v in c)


def _cs2_snapshot(t: float) -> dict:
    s = Cs2State(clock=lambda: t)
    s.update(demo_payloads(t))
    return s.snapshot()


def test_cs2_scene_takes_the_strip_only_for_the_bomb_and_flashes():
    theme = scenes.THEMES["cs2"]
    calm = scenes.cs2_frame(theme, _cs2_snapshot(10), 10, 71, strip_always=False)
    assert calm.strip is None                                   # ambilight stays
    assert calm.keyboard[2][3] == scenes.CT                     # W in CT blue
    bomb = scenes.cs2_frame(theme, _cs2_snapshot(45), 45, 71, strip_always=False)
    assert bomb.strip is not None and len(bomb.strip) == 71
    flashed = scenes.cs2_frame(theme, _cs2_snapshot(24.2), 24.2, 71, strip_always=False)
    assert flashed.strip is not None and min(flashed.keyboard[1]) > (200, 0, 0)


def test_effects_blend_and_end():
    f = Frame.blank(10)
    e = Effect("flash", (255, 255, 255), 0.0, 1.0)
    e.apply(f, 0.0)
    assert f.keyboard[0][0] == (255, 255, 255) and f.strip[0] == (255, 255, 255)
    assert e.done(1.0) and not e.done(0.5)
    for kind in ("fade", "strobe", "sweep", "burst"):
        g = Frame.blank(10)
        Effect(kind, (200, 50, 0), 0.0, 1.0).apply(g, 0.3)


class FakeLights:
    def __init__(self):
        self.scenes, self.calls = [], []

    def scene(self, keyboard, strip, glow):
        self.scenes.append((keyboard, strip, glow))

    def end_scene(self):
        self.calls.append("end")

    def restore(self, target="all"):
        self.calls.append(("restore", target))


class Clock:
    t = 0.0

    def __call__(self):
        return self.t


def test_director_follows_the_game_and_hands_back():
    fl, clock = FakeLights(), Clock()
    gl = GameLights(fl, clock=clock)
    assert gl.tick(0.0) is False and fl.scenes == []            # no game, nothing to draw
    gl.set_games({"beamng.drive.x64.exe"}, None)
    assert gl.tick(1.0) is True
    kb, strip, glow = fl.scenes[-1]
    assert len(kb) == 6 and strip is None and glow != BLACK     # events mode: the strip stays ambilight
    gl.add_effect(Effect("flash", (255, 0, 0), 1.0, 0.5))
    gl.tick(1.1)
    assert fl.scenes[-1][1] is not None                         # an effect borrows the strip
    gl.set_games(set(), None)
    assert gl.tick(3.0) is False and fl.calls == ["end"]


def test_director_static_frames_are_sent_once_and_resent_after_a_pause():
    fl = FakeLights()
    gl = GameLights(fl)
    gl.configure({"enabled": True, "strip": "events"})
    gl.theme = scenes.Theme("still", "Still", ("x.exe",), "#000000", "#ff0000", "#00ff00", "static")
    gl.tick(0.0)
    gl.tick(1.0)
    assert len(fl.scenes) == 1
    gl.tick(2.0, paused=True)
    gl.tick(3.0)
    assert len(fl.scenes) == 2


def test_native_chroma_games_keep_the_keyboard():
    fl = FakeLights()
    gl = GameLights(fl)
    gl.set_games({"tetriseffect-win64-shipping.exe"}, None)
    gl.configure({"strip": "theme"})
    gl.tick(0.0)
    kb, strip, glow = fl.scenes[-1]
    assert kb is None and glow is None and strip is not None
    assert ("restore", "chroma") in fl.calls
    gl2 = GameLights(FakeLights())
    gl2.configure({"native_chroma": ["*"]})
    assert gl2.native(scenes.THEMES["cs2"])


def test_disabled_or_switched_off_theme_shows_nothing():
    gl = GameLights(FakeLights())
    gl.configure({"enabled": False})
    assert gl.set_games({"cs2.exe"}, "cs2.exe") is None
    gl.configure({"off": ["cs2"]})
    assert gl.set_games({"cs2.exe"}, "cs2.exe") is None


def test_director_turns_cs2_events_into_effects():
    clock = Clock()
    state = Cs2State(clock=clock)

    class Tele:
        @property
        def data(self):
            return state.snapshot()

        def events_since(self, cursor):
            return state.events_since(cursor)

    fl = FakeLights()
    gl = GameLights(fl, clock=clock)
    gl.set_games({"cs2.exe"}, "cs2.exe")
    for i in range(141):                                        # 0 .. 14 s: the first kill at 14
        clock.t = i / 10
        state.update(demo_payloads(clock.t))
        gl.tick(clock.t, cs2=Tele())
    assert any(e.kind == "flash" for e in gl.effects)


def test_shift_lights_without_a_theme_draw_the_old_rev_bar():
    fl = FakeLights()
    gl = GameLights(fl)
    gl.tick(0.0, shift=0.5, n_leds=20)
    kb, strip, _ = fl.scenes[-1]
    assert strip is not None and strip[0] == hexc("#22c55e") and strip[10] == BLACK
    assert kb[0][3] == hexc("#22c55e")                          # F1 lit


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a, k))


def test_lights_scene_sends_glow_only_on_change_and_releases():
    strip, chroma = Recorder(), Recorder()
    lights = Lights(strip=strip, chroma=chroma)
    grid = [[(1, 2, 3)] * 22 for _ in range(6)]
    lights.scene(grid, [(9, 9, 9)] * 5, (10, 20, 30))
    sets = [c for c in chroma.calls if c[0] == "set"]
    assert len(sets) == 4 and chroma.calls[0][0] == "keyboard"
    assert chroma.calls[0][1][0][0][0] == 1 | 2 << 8 | 3 << 16  # BGR for the SDK
    lights.scene(grid, None, (10, 20, 30))
    assert len([c for c in chroma.calls if c[0] == "set"]) == 4  # same glow: not resent
    assert strip.calls[-1][0] == "restore"                       # strip handed back to ambilight
    lights.end_scene()
    assert chroma.calls[-1][0] == "release"
