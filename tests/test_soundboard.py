"""Soundboard: library processing, built-in sounds, mixing and the tap/hold behaviour."""
import os

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from freedock.profile import Slot  # noqa: E402
from freedock.soundboard import SAMPLE_RATE  # noqa: E402
from freedock.soundboard.library import Library, parse_time, process  # noqa: E402
from freedock.soundboard.mixer import NullMixer, Voice, _same_cable, mix_into, soft_clip  # noqa: E402
from freedock.soundboard.synth import BUILTINS  # noqa: E402
from freedock.soundboard.ytdl import start_from_url  # noqa: E402


@pytest.mark.parametrize("text,seconds", [("83", 83), ("1:23", 83), ("01:01:01", 3661), ("1m23s", 83),
                                          ("83.5", 83.5), ("", None), (None, None), (12, 12.0)])
def test_parse_time(text, seconds):
    assert parse_time(text) == seconds


def test_parse_time_rejects_garbage():
    with pytest.raises(ValueError):
        parse_time("abc")


def test_start_from_shared_links():
    assert start_from_url("https://youtu.be/abc?t=42") == 42
    assert start_from_url("https://www.youtube.com/watch?v=abc&t=1m5s") == 65
    assert start_from_url("https://www.youtube.com/watch?v=abc") is None


def test_process_trims_matches_loudness_and_never_clips():
    sr = SAMPLE_RATE
    t = np.arange(sr * 4) / sr
    quiet = np.column_stack([0.01 * np.sin(2 * np.pi * 440 * t)] * 2).astype(np.float32)
    out = process(quiet, start=1.0, end=3.0)
    assert abs(len(out) - 2 * sr) <= 1
    assert np.max(np.abs(out)) <= 0.891
    rms_db = 20 * np.log10(np.sqrt(np.mean(out ** 2)))
    assert -19.5 < rms_db < -16.5                     # quiet clip brought up to the library loudness
    assert abs(out[0, 0]) < 1e-3 and abs(out[-1, 0]) < 1e-3   # faded in/out: no clicks
    with pytest.raises(ValueError):
        process(quiet, start=10.0)


def test_every_builtin_sound_is_short_stereo_and_finite():
    for sid, (_names, icon, color, build) in BUILTINS.items():
        x = build()
        assert x.ndim == 2 and x.shape[1] == 2, sid
        assert 0.3 < len(x) / SAMPLE_RATE < 5, sid
        assert np.isfinite(x).all() and np.max(np.abs(x)) <= 1.0, sid


def test_library_round_trip(tmp_path):
    lib = Library(tmp_path)
    assert lib.ensure_builtins("pt") == len(BUILTINS)
    assert lib.ensure_builtins("pt") == 0              # only once, deleted ones stay deleted
    assert lib.get("airhorn").name == "Buzina" and lib.get("airhorn").builtin
    data = lib.samples("airhorn")
    assert data is not None and data.dtype == np.float32 and data.shape[1] == 2
    lib.update("airhorn", name="Corneta", gain_db=-6.0)
    again = Library(tmp_path)
    assert again.get("airhorn").name == "Corneta"
    assert np.max(np.abs(again.samples("airhorn"))) < np.max(np.abs(data))
    assert again.remove("airhorn") and again.get("airhorn") is None
    assert not (tmp_path / "airhorn.wav").exists()
    tone = np.column_stack([np.sin(np.arange(SAMPLE_RATE) / 10)] * 2).astype(np.float32) * 0.5
    s1 = again.add_samples("Meu Meme!", tone)
    s2 = again.add_samples("Meu Meme!", tone)
    assert s1.id == "meu_meme" and s2.id == "meu_meme_2"


def test_mixing_and_soft_clip():
    out = np.zeros((480, 2), dtype=np.float32)
    a = Voice("a", np.full((600, 2), 0.5, np.float32), "mic")
    b = Voice("b", np.full((300, 2), 0.5, np.float32), "mic")
    done = mix_into(out, [a, b], 1.0)
    assert done == [b] and a.pos == 480
    assert out[0, 0] == pytest.approx(1.0) and out[400, 0] == pytest.approx(0.5)
    clipped = soft_clip(out.copy())
    assert np.max(np.abs(clipped)) < 1.0 and clipped[400, 0] == pytest.approx(0.5)


def test_tap_toggles_and_no_cable_falls_back_to_headset(tmp_path):
    lib = Library(tmp_path)
    lib.ensure_builtins("en")
    m = NullMixer(lib)
    assert m.play("rimshot", "mic") == "no_cable"      # no virtual cable: still audible, on the headset
    assert m.playing()["rimshot"][0] == "local"
    assert m.play("rimshot", "local") == "stopped"     # same sound again stops it
    assert m.playing() == {}
    assert m.play("nope", "mic") == "missing"
    m._cable_stream = object()                          # pretend a cable is open
    m.cfg["monitor"] = True
    assert m.play("tada", "mic") == "playing"
    assert sorted(v.bus for v in m.voices) == ["local", "mic"]   # monitor copy for yourself
    assert m.play("tada", "mic") == "stopped" and m.voices == []


def test_sound_keys_hold_for_the_headset():
    from freedock.engine import Engine
    key = Slot(action={"type": "sound", "sound": "airhorn", "target": "mic", "hold": "local"})
    assert Engine._long_action(key) == {"type": "sound", "sound": "airhorn", "target": "local"}
    assert Engine._long_action(Slot(action={"type": "sound", "sound": "x", "hold": "none"})) is None
    explicit = Slot(action={"type": "sound", "sound": "x"}, long_action={"type": "page", "target": "next"})
    assert Engine._long_action(explicit) == {"type": "page", "target": "next"}


def test_cable_loop_detection():
    assert _same_cable("Microphone (Steam Streaming Microphone)", "Speakers (Steam Streaming Microphone)")
    assert _same_cable("CABLE Output (VB-Audio Virtual Cable)", "CABLE Input (VB-Audio Virtual Cable)")
    assert not _same_cable("Headset Microphone (Astro A50 Voice)", "CABLE Input (VB-Audio Virtual Cable)")


def test_decode_wav_and_m4a_roundtrip(tmp_path):
    from PySide6.QtCore import QCoreApplication
    QCoreApplication.instance() or QCoreApplication([])
    from freedock.soundboard.decode import decode_file, write_wav
    x = (0.3 * np.column_stack([np.sin(np.arange(SAMPLE_RATE) * 0.05)] * 2)).astype(np.float32)
    write_wav(tmp_path / "a.wav", x)
    y = decode_file(tmp_path / "a.wav")
    assert y.shape == x.shape and np.max(np.abs(y - x)) < 1e-3
