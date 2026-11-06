"""Built-in sounds, synthesised from scratch (no samples, no copyright): a starter pack so the
Sounds page works out of the box. Every function returns float32 stereo at 48 kHz."""
from __future__ import annotations

import numpy as np

from . import SAMPLE_RATE as SR

_rng = np.random.default_rng(76)


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(SR * seconds)) / SR


def _env(n: int, attack: float = 0.01, release: float = 0.05) -> np.ndarray:
    e = np.ones(n, dtype=np.float32)
    a, r = max(1, int(SR * attack)), max(1, int(SR * release))
    e[:a] = np.linspace(0, 1, a)
    e[-r:] *= np.linspace(1, 0, r)
    return e


def _lowpass(x: np.ndarray, cutoff: float) -> np.ndarray:
    """One-pole low-pass (vectorised via an IIR loop on blocks would be overkill for clips)."""
    a = np.exp(-2 * np.pi * cutoff / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i, v in enumerate(x):
        acc = (1 - a) * v + a * acc
        y[i] = acc
    return y


def _highpass(x: np.ndarray) -> np.ndarray:
    return np.diff(x, prepend=x[:1]).astype(np.float32)


def _brass(freq: np.ndarray | float, seconds: float, bright: int = 9) -> np.ndarray:
    t = _t(seconds)
    f = np.broadcast_to(np.asarray(freq, dtype=np.float64), t.shape)
    phase = 2 * np.pi * np.cumsum(f) / SR
    out = sum(np.sin(k * phase) / k ** 1.1 for k in range(1, bright + 1))
    return out.astype(np.float32)


def _stereo(x: np.ndarray, level: float = 0.9) -> np.ndarray:
    x = x.astype(np.float32)
    peak = float(np.max(np.abs(x))) or 1.0
    x = x / peak * level
    return np.column_stack([x, x])


def _place(total: np.ndarray, part: np.ndarray, at: float) -> None:
    i = int(at * SR)
    j = min(len(total), i + len(part))
    total[i:j] += part[: j - i]


def airhorn() -> np.ndarray:
    out = np.zeros(int(SR * 1.9), dtype=np.float32)
    for start, dur in ((0.0, 0.22), (0.3, 0.22), (0.6, 1.2)):
        t = _t(dur)
        vib = 1 + 0.004 * np.sin(2 * np.pi * 6 * t)
        blast = sum(_brass(f * vib, dur, 14) for f in (466.2, 554.4, 698.5))
        blast = np.tanh(blast * 1.6) * _env(len(t), 0.01, 0.06)
        _place(out, blast, start)
    return _stereo(out)


def rimshot() -> np.ndarray:
    out = np.zeros(int(SR * 1.8), dtype=np.float32)
    for at, f0 in ((0.0, 190.0), (0.17, 140.0)):
        t = _t(0.25)
        f = f0 * (1 + 0.6 * np.exp(-t * 30))
        tom = np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 14)
        tom += 0.3 * _rng.standard_normal(len(t)) * np.exp(-t * 60)
        _place(out, tom.astype(np.float32), at)
    t = _t(1.3)
    cym = _highpass(_highpass(_rng.standard_normal(len(t)).astype(np.float32))) * np.exp(-t * 3.2)
    _place(out, 0.5 * cym, 0.42)
    return _stereo(out)


def sad_trombone() -> np.ndarray:
    notes = ((392.0, 0.42), (370.0, 0.42), (349.2, 0.42), (329.6, 1.3))
    out = np.zeros(int(SR * 2.75), dtype=np.float32)
    at = 0.0
    for i, (f, dur) in enumerate(notes):
        t = _t(dur)
        vib = 1 + (0.02 * np.sin(2 * np.pi * 5.5 * t) * np.clip(t / 0.3, 0, 1) if i == 3 else 0)
        slide = f * (1 - 0.012 * np.exp(-t * 18)) * vib
        tone = _brass(slide, dur, 8) * _env(len(t), 0.04, 0.12)
        _place(out, _lowpass(tone, 1800), at)
        at += dur + 0.03
    return _stereo(out)


def tada() -> np.ndarray:
    out = np.zeros(int(SR * 1.9), dtype=np.float32)
    for i, f in enumerate((523.3, 659.3, 784.0)):
        t = _t(0.09)
        _place(out, _brass(f, 0.09, 10) * _env(len(t), 0.005, 0.02), i * 0.08)
    t = _t(1.5)
    chord = sum(_brass(f * (1 + 0.003 * np.sin(2 * np.pi * 5 * t)), 1.5, 10) for f in (523.3, 659.3, 784.0, 1046.5))
    _place(out, chord * _env(len(t), 0.01, 0.6), 0.3)
    return _stereo(out)


def censor_beep() -> np.ndarray:
    t = _t(0.8)
    return _stereo(np.sin(2 * np.pi * 1000 * t) * _env(len(t), 0.005, 0.01), 0.7)


def drum_roll() -> np.ndarray:
    out = np.zeros(int(SR * 3.3), dtype=np.float32)
    at, gap = 0.0, 0.11
    while at < 2.2:
        t = _t(0.06)
        hit = _highpass(_rng.standard_normal(len(t)).astype(np.float32)) * np.exp(-t * 55)
        _place(out, hit * (0.5 + 0.5 * at / 2.2), at)
        at += gap
        gap = max(0.035, gap * 0.95)
    t = _t(1.1)
    crash = _highpass(_rng.standard_normal(len(t)).astype(np.float32)) * np.exp(-t * 2.5)
    boom = np.sin(2 * np.pi * 70 * t) * np.exp(-t * 6)
    _place(out, 1.4 * crash + 2.0 * boom, 2.2)
    return _stereo(out)


def applause() -> np.ndarray:
    seconds = 3.2
    out = np.zeros(int(SR * seconds), dtype=np.float32)
    for _ in range(900):
        at = _rng.uniform(0, seconds - 0.05)
        t = _t(0.02)
        clap = _highpass(_rng.standard_normal(len(t)).astype(np.float32)) * np.exp(-t * 200)
        _place(out, clap * _rng.uniform(0.3, 1.0), at)
    out *= _env(len(out), 0.4, 1.0)
    return _stereo(out)


def crickets() -> np.ndarray:
    out = np.zeros(int(SR * 3.0), dtype=np.float32)
    for burst in np.arange(0.1, 2.8, 0.55):
        for k in range(3):
            t = _t(0.04)
            chirp = np.sin(2 * np.pi * 4600 * t) * np.sin(np.pi * t / 0.04)
            _place(out, chirp.astype(np.float32), burst + k * 0.06)
    return _stereo(out, 0.45)


BUILTINS = {
    # id: (name per language, icon, colour, builder)
    "airhorn": ({"en": "Air horn", "pt": "Buzina", "es": "Bocina"}, "megaphone", "#ef4444", airhorn),
    "rimshot": ({"en": "Ba dum tss", "pt": "Tum dum tss", "es": "Ba dum tss"}, "drum", "#f59e0b", rimshot),
    "sad_trombone": ({"en": "Sad trombone", "pt": "Trombone triste", "es": "Trombón triste"}, "frown",
                     "#8b5cf6", sad_trombone),
    "tada": ({"en": "Ta-da!", "pt": "Tcharam!", "es": "¡Tachán!"}, "party-popper", "#22c55e", tada),
    "beep": ({"en": "Censor beep", "pt": "Piii (censura)", "es": "Pitido"}, "volume-x", "#64748b", censor_beep),
    "drum_roll": ({"en": "Drum roll", "pt": "Rufar", "es": "Redoble"}, "drum", "#0ea5e9", drum_roll),
    "applause": ({"en": "Applause", "pt": "Aplausos", "es": "Aplausos"}, "hand-metal", "#eab308", applause),
    "crickets": ({"en": "Crickets", "pt": "Grilos", "es": "Grillos"}, "bug", "#16a34a", crickets),
}
