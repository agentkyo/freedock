"""The sound library: WAV files in ``<config>/sounds`` plus an index (``sounds.json``).

Every sound is stored already processed - trimmed, faded, loudness-matched, 48 kHz stereo 16-bit -
so playback never decodes or resamples anything and all memes come out at a similar volume.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..profile import config_dir
from . import SAMPLE_RATE
from .decode import decode_file, read_wav, write_wav

log = logging.getLogger(__name__)

MAX_SECONDS = 300            # a meme, not an album
TARGET_RMS_DB = -18.0        # loudness the library is matched to
PEAK = 0.89                  # -1 dBFS


@dataclass
class Sound:
    id: str
    name: str
    file: str                          # file name inside the library folder
    duration: float = 0.0
    icon: str = "audio-lines"
    color: str = ""
    gain_db: float = 0.0               # per-sound trim on top of the loudness match
    source: str = ""                   # original link or file
    start: float = 0.0
    end: float | None = None
    builtin: bool = False
    created: float = field(default_factory=time.time)


def display_name(sound: "Sound") -> str:
    """Built-in sounds follow the app language until the user renames them."""
    if sound.builtin:
        from ..i18n import language
        from .synth import BUILTINS
        names = BUILTINS.get(sound.id, ({},))[0]
        if sound.name in names.values():
            return names.get(language()) or names.get("en") or sound.name
    return sound.name


def library_dir() -> Path:
    return config_dir() / "sounds"


def slug(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text).strip("_").lower()
    return text[:40] or "sound"


def parse_time(value) -> float | None:
    """'83', '1:23', '01:02:03', '1m23s', '83.5' -> seconds; '' / None -> None."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    if not s:
        return None
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+(?:\.\d+)?)s?)?", s)
    if m and any(m.groups()):
        h, mi, se = m.groups()
        return int(h or 0) * 3600 + int(mi or 0) * 60 + float(se or 0)
    parts = s.split(":")
    try:
        total = 0.0
        for p in parts:
            total = total * 60 + float(p)
        return total
    except ValueError:
        raise ValueError(f"not a time: {value!r}") from None


def process(samples: np.ndarray, start: float | None = None, end: float | None = None,
            normalize: bool = True) -> np.ndarray:
    """Trim, cap the length, match loudness and add 8 ms fades (no clicks)."""
    a = int((start or 0.0) * SAMPLE_RATE)
    b = int(end * SAMPLE_RATE) if end else len(samples)
    x = samples[max(0, a):max(a + 1, b)]
    x = x[: MAX_SECONDS * SAMPLE_RATE].astype(np.float32, copy=True)
    if len(x) == 0:
        raise ValueError("the selected part is empty")
    if normalize:
        rms = float(np.sqrt(np.mean(x ** 2))) or 1e-9
        gain = 10 ** (TARGET_RMS_DB / 20) / rms
        peak = float(np.max(np.abs(x))) or 1e-9
        gain = min(gain, PEAK / peak)        # never clip: loud memes stay a bit quieter instead
        x *= gain
    fade = min(len(x) // 4, int(0.008 * SAMPLE_RATE))
    if fade > 1:
        ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)[:, None]
        x[:fade] *= ramp
        x[-fade:] *= ramp[::-1]
    return x


class Library:
    def __init__(self, root: Path | None = None):
        self.root = root or library_dir()
        self._lock = threading.RLock()
        self._cache: dict[str, np.ndarray] = {}
        self.sounds: dict[str, Sound] = {}
        self.version = 0                     # bumps on every change (editors refresh on it)
        self._checked = time.monotonic()
        self._mtime = None
        self.load()
        try:
            self._mtime = self.index_path.stat().st_mtime
        except OSError:
            pass

    # ---------------------------------------------------------------- index
    @property
    def index_path(self) -> Path:
        return self.root / "sounds.json"

    def load(self) -> None:
        with self._lock:
            self.sounds = {}
            try:
                data = json.loads(self.index_path.read_text(encoding="utf-8"))
                known = set(Sound.__dataclass_fields__)
                for d in data.get("sounds", []):
                    s = Sound(**{k: v for k, v in d.items() if k in known})
                    self.sounds[s.id] = s
            except FileNotFoundError:
                pass
            except (OSError, ValueError, TypeError) as exc:
                log.warning("sound library index unreadable: %s", exc)
            self.version += 1

    def save(self) -> None:
        with self._lock:
            self.root.mkdir(parents=True, exist_ok=True)
            tmp = self.index_path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"version": 1, "sounds": [asdict(s) for s in self.sounds.values()]},
                                      ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self.index_path)
            self.version += 1
            try:
                self._mtime = self.index_path.stat().st_mtime   # our own write is not "external"
            except OSError:
                pass

    def _maybe_reload(self) -> None:
        """Pick up changes made by another process (``freedock sound add``), checked every 2 s at most."""
        now = time.monotonic()
        if now - self._checked < 2.0:
            return
        self._checked = now
        try:
            mtime = self.index_path.stat().st_mtime
        except OSError:
            return
        if mtime != self._mtime:
            self._mtime = mtime
            self.load()
            self._cache.clear()

    def list(self) -> list[Sound]:
        self._maybe_reload()
        with self._lock:
            return sorted(self.sounds.values(), key=lambda s: (not s.builtin, s.created))

    def get(self, sid: str | None) -> Sound | None:
        self._maybe_reload()
        return self.sounds.get(sid or "")

    def path(self, sound: Sound) -> Path:
        return self.root / sound.file

    # ---------------------------------------------------------------- samples
    def samples(self, sid: str) -> np.ndarray | None:
        """Playback-ready samples (gain applied), cached."""
        with self._lock:
            if sid in self._cache:
                return self._cache[sid]
            s = self.sounds.get(sid)
        if s is None:
            return None
        try:
            data = read_wav(self.path(s))
        except Exception as exc:  # noqa: BLE001 - missing / corrupt file
            log.warning("sound %s unreadable: %s", sid, exc)
            return None
        if s.gain_db:
            data = np.clip(data * 10 ** (s.gain_db / 20), -1.0, 1.0).astype(np.float32)
        with self._lock:
            if len(self._cache) > 48:
                self._cache.pop(next(iter(self._cache)))
            self._cache[sid] = data
        return data

    # ---------------------------------------------------------------- changes
    def _new_id(self, name: str) -> str:
        base = slug(name)
        sid, n = base, 2
        while sid in self.sounds:
            sid, n = f"{base}_{n}", n + 1
        return sid

    def add_samples(self, name: str, samples: np.ndarray, *, source: str = "", start: float = 0.0,
                    end: float | None = None, icon: str = "audio-lines", color: str = "",
                    builtin: bool = False, sid: str | None = None, normalize: bool = True) -> Sound:
        data = process(samples, start, end, normalize)
        with self._lock:
            sid = sid or self._new_id(name)
            self.root.mkdir(parents=True, exist_ok=True)
            fname = f"{sid}.wav"
            write_wav(self.root / fname, data)
            s = Sound(id=sid, name=name.strip() or sid, file=fname, duration=round(len(data) / SAMPLE_RATE, 2),
                      icon=icon, color=color, source=source, start=start or 0.0, end=end, builtin=builtin)
            self.sounds[sid] = s
            self._cache.pop(sid, None)
            self.save()
        log.info("sound added: %s (%.1f s)", sid, s.duration)
        return s

    def add_file(self, path: str | Path, name: str = "", start=None, end=None) -> Sound:
        path = Path(path)
        samples = decode_file(path)
        return self.add_samples(name or path.stem, samples, source=str(path),
                                start=parse_time(start) or 0.0, end=parse_time(end))

    def update(self, sid: str, **changes) -> Sound | None:
        with self._lock:
            s = self.sounds.get(sid)
            if s is None:
                return None
            for k, v in changes.items():
                if hasattr(s, k) and k not in ("id", "file"):
                    setattr(s, k, v)
            self._cache.pop(sid, None)
            self.save()
            return s

    def remove(self, sid: str) -> bool:
        with self._lock:
            s = self.sounds.pop(sid, None)
            if s is None:
                return False
            self._cache.pop(sid, None)
            try:
                self.path(s).unlink()
            except OSError:
                pass
            self.save()
            return True

    def ensure_builtins(self, lang: str = "en", force: bool = False) -> int:
        """Create the synthesised starter sounds once (deleted ones are not brought back)."""
        from .synth import BUILTINS
        marker = self.root / ".builtins"
        if marker.exists() and not force:
            return 0
        made = 0
        for sid, (names, icon, color, build) in BUILTINS.items():
            if sid in self.sounds and self.path(self.sounds[sid]).is_file():
                continue
            try:
                self.add_samples(names.get(lang) or names["en"], build(), source="FreeDock", icon=icon,
                                 color=color, builtin=True, sid=sid, normalize=True)
                made += 1
            except Exception:  # noqa: BLE001 - a broken generator must not stop the others
                log.exception("built-in sound %s", sid)
        self.root.mkdir(parents=True, exist_ok=True)
        marker.write_text("1", encoding="utf-8")
        return made


_library: Library | None = None


def library() -> Library:
    global _library
    if _library is None:
        _library = Library()
    return _library
