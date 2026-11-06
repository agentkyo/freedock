"""Decode any audio file to float32 stereo samples at 48 kHz.

Qt Multimedia ships with FFmpeg, so MP3, M4A/AAC, Opus/WebM, OGG, FLAC and WAV all work without
installing anything. The decoder needs an event loop; :func:`decode_file` runs a private one, so it
can be called from any thread (Qt adopts plain Python threads).
"""
from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

from . import CHANNELS, SAMPLE_RATE


class DecodeError(Exception):
    pass


def read_wav(path: str | Path) -> np.ndarray:
    """Fast path for our own library files (16-bit PCM WAV)."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise DecodeError("not 16-bit PCM")
        rate, ch = w.getframerate(), w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    data = data.reshape(-1, ch)
    data = to_stereo(data)
    if rate != SAMPLE_RATE:
        data = resample(data, rate, SAMPLE_RATE)
    return data


def write_wav(path: str | Path, samples: np.ndarray) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2")
    tmp = Path(str(path) + ".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(CHANNELS)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)


def to_stereo(data: np.ndarray) -> np.ndarray:
    if data.ndim == 1:
        data = data[:, None]
    if data.shape[1] == 1:
        return np.repeat(data, 2, axis=1)
    return data[:, :2].copy() if data.shape[1] > 2 else data


def resample(data: np.ndarray, src: int, dst: int) -> np.ndarray:
    """Linear interpolation - fine for short sound effects."""
    if src == dst or len(data) == 0:
        return data
    n = int(round(len(data) * dst / src))
    x_old = np.linspace(0.0, 1.0, len(data), endpoint=False)
    x_new = np.linspace(0.0, 1.0, n, endpoint=False)
    return np.column_stack([np.interp(x_new, x_old, data[:, c]) for c in range(data.shape[1])]).astype(np.float32)


def decode_file(path: str | Path, timeout_s: float = 120.0) -> np.ndarray:
    path = Path(path)
    if not path.is_file():
        raise DecodeError(f"file not found: {path}")
    if path.suffix.lower() == ".wav":
        try:
            return read_wav(path)
        except (wave.Error, DecodeError, EOFError):
            pass                        # float / 24-bit / odd WAVs: let FFmpeg handle them
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

    if QCoreApplication.instance() is None:
        raise DecodeError("decoding needs a running Qt application")
    fmt = QAudioFormat()
    fmt.setSampleRate(SAMPLE_RATE)
    fmt.setChannelCount(CHANNELS)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    dec = QAudioDecoder()
    dec.setAudioFormat(fmt)
    dec.setSource(QUrl.fromLocalFile(str(path)))
    chunks: list[np.ndarray] = []
    state = {"error": None, "rate": SAMPLE_RATE, "ch": CHANNELS}
    loop = QEventLoop()

    def ready():
        buf = dec.read()
        f = buf.format()
        state["rate"], state["ch"] = f.sampleRate() or SAMPLE_RATE, f.channelCount() or CHANNELS
        raw = bytes(buf.constData())
        if f.sampleFormat() == QAudioFormat.SampleFormat.Float:
            arr = np.frombuffer(raw, dtype=np.float32)
        elif f.sampleFormat() == QAudioFormat.SampleFormat.Int16:
            arr = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
        elif f.sampleFormat() == QAudioFormat.SampleFormat.Int32:
            arr = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
        else:  # UInt8
            arr = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
        chunks.append(arr.copy())

    def failed(_err):
        state["error"] = dec.errorString() or "decoder error"
        loop.quit()

    dec.bufferReady.connect(ready)
    dec.finished.connect(loop.quit)
    dec.error.connect(failed)
    timer = QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(loop.quit)
    timer.start(int(timeout_s * 1000))
    dec.start()
    loop.exec()
    dec.stop()
    if state["error"]:
        raise DecodeError(state["error"])
    if not chunks:
        raise DecodeError("no audio in this file")
    data = np.concatenate(chunks).reshape(-1, state["ch"])
    data = to_stereo(data)
    if state["rate"] != SAMPLE_RATE:
        data = resample(data, state["rate"], SAMPLE_RATE)
    return data.astype(np.float32, copy=False)
