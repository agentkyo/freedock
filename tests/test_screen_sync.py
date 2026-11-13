"""Keyboard screen sync: color math, the sampler, and GameLights handing over between games and the screen."""
import time

import numpy as np

from freedock.integrations import scenes, screen_sync
from freedock.integrations.game_lights import GameLights
from freedock.integrations.screen_sync import COLS, FLOOR, ROWS, ScreenSampler, glow_of, lift, to_grid, tune


def picture(left=(255, 0, 0), right=(0, 0, 255), h=96, w=176):
    a = np.zeros((h, w, 3), np.uint8)
    a[:, : w // 2] = left
    a[:, w // 2:] = right
    return a


def test_grid_is_a_small_copy_of_the_screen():
    g = to_grid(picture())
    assert g.shape == (ROWS, COLS, 3)
    assert tuple(g[0, 0]) == (255, 0, 0) and tuple(g[5, 21]) == (0, 0, 255)
    assert to_grid(np.zeros((3, 5, 3), np.uint8)).shape == (ROWS, COLS, 3)   # tiny input still works


def test_tune_crushes_blacks_and_saturates():
    g = tune(np.array([[[4, 4, 4], [200, 100, 100]]], np.float32))
    assert g[0, 0].max() == 0
    assert g[0, 1][0] - g[0, 1][1] > 100                         # more saturated than before


def test_a_dark_screen_never_turns_the_keyboard_off():
    black = np.zeros((ROWS, COLS, 3), np.float32)
    lit = lift(black)
    assert lit.max(axis=-1).min() >= FLOOR - 1                   # every key glows, FreeDock blue
    assert lit[0, 0][2] > lit[0, 0][0]
    dim_red = np.full((ROWS, COLS, 3), (10, 0, 0), np.float32)
    assert lift(dim_red)[0, 0][0] >= FLOOR - 1 and lift(dim_red)[0, 0][2] == 0   # the picture's own hue
    bright = np.full((ROWS, COLS, 3), (200, 50, 0), np.float32)
    assert np.allclose(lift(bright), bright)                      # bright keys untouched


def test_glow_keeps_the_hue_at_a_usable_brightness():
    g = np.full((ROWS, COLS, 3), (40, 0, 20), np.float32)
    r, gr, b = glow_of(g)
    assert r >= 84 and gr == 0 and 0 < b < r


def test_sampler_smooths_and_survives_capture_failures():
    s = ScreenSampler(opener=lambda: None, smoothing_ms=80)
    s.feed(picture((255, 0, 0), (255, 0, 0)), 0.0)
    assert s.latest()[0][0][0][0] > 240
    s.feed(picture((0, 0, 255), (0, 0, 255)), 0.016)            # one frame later: still mostly red
    kb, _ = s.latest()
    assert kb[0][0][0] > kb[0][0][2]
    for i in range(30):
        s.feed(None, 0.05 + i * 0.05)                              # capture lost (locked screen...)
    assert max(s.latest()[0][0][0]) <= FLOOR + 1                   # ...drifts to the dim floor, never off


def test_sampler_thread_runs_and_reports_failure():
    shots = []

    def opener():
        def grab():
            shots.append(1)
            return picture()
        return grab, lambda: shots.append("closed")

    s = ScreenSampler(opener=opener, fps=100)
    s.start()
    deadline = time.monotonic() + 2
    while s.latest() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert s.latest() is not None
    s.stop()
    assert shots[-1] == "closed" and s.latest() is None
    dead = ScreenSampler(opener=lambda: None)
    dead.start()
    dead._thread.join(1)
    assert dead.failed and not dead.running


def test_make_sampler_is_off_in_tests():
    assert screen_sync.make_sampler() is None


class FakeLights:
    def __init__(self):
        self.scenes, self.calls = [], []

    def scene(self, keyboard, strip, glow):
        self.scenes.append((keyboard, strip, glow))

    def end_scene(self):
        self.calls.append("end")

    def restore(self, target="all"):
        self.calls.append(("restore", target))


class FakeSampler:
    def __init__(self):
        self.running = False
        self.failed = False
        self.frame = None

    def start(self):
        self.running = True

    def stop(self):
        self.running = False

    def latest(self):
        return self.frame if self.running else None


def test_without_a_game_the_keyboard_follows_the_screen():
    fl, fs = FakeLights(), FakeSampler()
    gl = GameLights(fl, sampler_factory=lambda: fs)
    assert gl.tick(0.0) is False and fs.running and fl.scenes == [] and fl.calls == []   # warming up
    kb = [[(10, 20, 30)] * COLS for _ in range(ROWS)]
    fs.frame = (kb, (90, 90, 90))
    assert gl.tick(1.0) is True
    keyboard, strip, glow = fl.scenes[-1]
    assert keyboard[0][0] == (10, 20, 30) and strip is None and glow == (90, 90, 90)  # strip stays ambilight

    gl.set_games({"beamng.drive.x64.exe"}, None)                  # a game: its theme, sampler paused
    gl.tick(2.0)
    assert not fs.running and fl.scenes[-1][0][0][0] != (10, 20, 30)
    gl.set_games(set(), None)                                      # back to the desktop
    gl.tick(3.0)
    fs.frame = (kb, (90, 90, 90))
    gl.tick(4.0)
    assert fs.running and fl.scenes[-1][0][0][0] == (10, 20, 30)

    gl.configure({"idle": "off"})                                  # switched off: back to Synapse
    assert gl.tick(5.0) is False and not fs.running and fl.calls[-1] == "end"


def test_glow_changes_are_rate_limited():
    fl, fs = FakeLights(), FakeSampler()
    gl = GameLights(fl, sampler_factory=lambda: fs)
    kb = [[(10, 20, 30)] * COLS for _ in range(ROWS)]
    fs.start()
    fs.frame = (kb, (90, 0, 0))
    gl.tick(0.0)
    fs.frame = (kb, (0, 90, 0))
    gl.tick(0.1)
    assert fl.scenes[-1][2] == (90, 0, 0)
    gl.tick(0.4)
    assert fl.scenes[-1][2] == (0, 90, 0)


def test_no_sampler_means_the_old_behaviour():
    fl = FakeLights()
    gl = GameLights(fl, sampler_factory=lambda: None)
    assert gl.tick(0.0) is False and fl.scenes == []
    gl.force("cs2")
    assert gl.tick(1.0) is True
    assert scenes.THEMES["cs2"]
