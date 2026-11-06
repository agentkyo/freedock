"""Real-time playback with two buses.

* **mic** - what your friends hear. Voice chat apps (Discord, Teams, games) take *one* input
  device, so FreeDock reads your real microphone, mixes the sounds on top and writes the result to
  a virtual audio cable; the chat app uses the cable's other end as its microphone.
  Supported cables, picked automatically: VB-Audio CABLE, Voicemeeter, Steam Streaming Microphone.
* **local** - your default output (headset/speakers), only you hear it.

Both are PortAudio callback streams (WASAPI on Windows). The mic passthrough goes through a small
jitter buffer (~20-60 ms) between the capture and the cable streams.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from . import CHANNELS, SAMPLE_RATE

log = logging.getLogger(__name__)

BLOCK = 480                       # 10 ms
JITTER_MAX = 6 * BLOCK            # mic frames kept at most before dropping (bounds the latency)
JITTER_START = 2 * BLOCK          # wait for this much before starting the passthrough
LOCAL_IDLE_S = 8.0                # close the headset stream after this long without sounds

# PortAudio must not open/close streams or scan devices from two threads at once (WASAPI fails with
# "WdmSyncIoctl ... GLE 0x490"): every such call goes through this lock.
PA_LOCK = threading.RLock()

# render (output) side of known virtual cables, best first
CABLES = ("CABLE Input", "CABLE In 16ch", "Voicemeeter Input", "VoiceMeeter Input", "VB-Audio Virtual Cable",
          "Steam Streaming Microphone")


@dataclass
class Voice:
    sid: str
    data: np.ndarray
    bus: str                       # mic | local
    gain: float = 1.0
    pos: int = 0
    started: float = 0.0

    @property
    def progress(self) -> float:
        return min(1.0, self.pos / max(1, len(self.data)))


def mix_into(out: np.ndarray, voices: list[Voice], gain: float) -> list[Voice]:
    """Add every voice's next block into ``out`` (in place). Returns the voices that finished."""
    n = len(out)
    done = []
    for v in voices:
        chunk = v.data[v.pos:v.pos + n]
        out[:len(chunk)] += chunk * (v.gain * gain)
        v.pos += len(chunk)
        if v.pos >= len(v.data):
            done.append(v)
    return done


def soft_clip(x: np.ndarray) -> np.ndarray:
    """Transparent below ~-3 dBFS, then a smooth knee - several memes at once never crackle."""
    over = np.abs(x) > 0.7
    if over.any():
        x[over] = np.sign(x[over]) * (0.7 + 0.3 * np.tanh((np.abs(x[over]) - 0.7) / 0.3))
    return x


class Mixer:
    def __init__(self, library, config: dict | None = None):
        self.lib = library
        self.cfg: dict = {"cable": "", "mic": "", "passthrough": True, "monitor": False, "monitor_volume": 0.6,
                          "volume": 0.8, "mic_gain": 1.0}
        self.cfg.update(config or {})
        self._lock = threading.Lock()
        self.voices: list[Voice] = []
        self._jitter: deque = deque()
        self._jitter_frames = 0
        self._primed = False
        self._cable_stream = None
        self._mic_stream = None
        self._local_stream = None
        self._local_last = 0.0
        self.status: dict = {"cable": None, "mic": None, "error": None, "passthrough": False}
        self._sd = None
        self._started = False
        self._house: threading.Thread | None = None

    # ---------------------------------------------------------------- devices
    def _sounddevice(self):
        if self._sd is None:
            import sounddevice as sd
            self._sd = sd
        return self._sd

    def _hostapi(self) -> int | None:
        sd = self._sounddevice()
        if sys.platform == "win32":
            for i, h in enumerate(sd.query_hostapis()):
                if h["name"] == "Windows WASAPI":
                    return i
        return None

    def devices(self, kind: str) -> list[tuple[int, str]]:
        """(index, name) of usable output (kind='output') or input devices."""
        sd = self._sounddevice()
        api = self._hostapi()
        out = []
        with PA_LOCK:
            found = list(enumerate(sd.query_devices()))
        for i, d in found:
            if (api is None or d["hostapi"] == api) and d[f"max_{kind}_channels"] > 0:
                out.append((i, d["name"]))
        return out

    def _default(self, kind: str) -> int | None:
        sd = self._sounddevice()
        api = self._hostapi()
        if api is not None:
            idx = sd.query_hostapis(api)[f"default_{kind}_device"]
            return idx if idx >= 0 else None
        dev = sd.default.device[0 if kind == "input" else 1]
        return dev if dev is not None and dev >= 0 else None

    def find_cable(self) -> tuple[int, str] | None:
        outs = self.devices("output")
        want = (self.cfg.get("cable") or "").strip()
        if want:
            for i, name in outs:
                if name == want or want.lower() in name.lower():
                    return i, name
            return None
        for key in CABLES:
            for i, name in outs:
                if key.lower() in name.lower():
                    return i, name
        return None

    @staticmethod
    def cable_capture_name(render_name: str) -> str:
        """What the chat app should pick as its microphone for a given cable render device."""
        if "steam streaming microphone" in render_name.lower():
            return "Microphone (Steam Streaming Microphone)"
        if "cable" in render_name.lower():
            return "CABLE Output (VB-Audio Virtual Cable)"
        if "voicemeeter" in render_name.lower():
            return "Voicemeeter Out B1"
        return render_name

    def find_mic(self, cable_name: str | None) -> tuple[int, str] | None:
        ins = dict(self.devices("input"))
        want = (self.cfg.get("mic") or "").strip()
        if want:
            for i, name in ins.items():
                if name == want or want.lower() in name.lower():
                    return i, name
            return None
        idx = self._default("input")
        if idx is None or idx not in ins:
            return None
        name = ins[idx]
        # never loop the cable back into itself (the chat app's input set as Windows' default mic)
        if cable_name and _same_cable(name, cable_name):
            log.warning("default microphone is the virtual cable itself - passthrough disabled")
            return None
        return idx, name

    def _extra(self):
        sd = self._sounddevice()
        if sys.platform == "win32":
            try:
                return sd.WasapiSettings(auto_convert=True)
            except TypeError:
                return sd.WasapiSettings()
        return None

    # ---------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._started:
            return
        self._started = True
        with PA_LOCK:
            self._open_mic_bus()
        self._house = threading.Thread(target=self._housekeeping, name="soundboard", daemon=True)
        self._house.start()

    def restart(self, config: dict | None = None) -> None:
        if config is not None:
            self.cfg.update(config)
        with PA_LOCK:
            self._close_mic_bus()
            if self._started:
                self._open_mic_bus()

    def stop(self) -> None:
        self._started = False
        with PA_LOCK:
            self._close_mic_bus()
            self._close_local()

    def _open_mic_bus(self) -> None:
        sd = self._sounddevice()
        self.status.update(cable=None, mic=None, error=None, passthrough=False)
        try:
            cable = self.find_cable()
        except Exception as exc:  # noqa: BLE001 - PortAudio init failure
            self.status["error"] = str(exc)
            log.warning("soundboard: audio devices unavailable: %s", exc)
            return
        if cable is None:
            log.info("soundboard: no virtual cable found - sounds play on the headset only")
            return
        self.status["cable"] = cable[1]
        try:
            self._cable_stream = sd.OutputStream(device=cable[0], samplerate=SAMPLE_RATE, channels=CHANNELS,
                                                 dtype="float32", blocksize=BLOCK, latency="low",
                                                 extra_settings=self._extra(), callback=self._cable_cb)
            self._cable_stream.start()
        except Exception as exc:  # noqa: BLE001
            self.status["error"] = f"{cable[1]}: {exc}"
            log.warning("soundboard: cannot open %s: %s", cable[1], exc)
            self._cable_stream = None
            return
        if not self.cfg.get("passthrough", True):
            return
        mic = self.find_mic(cable[1])
        if mic is None:
            return
        self.status["mic"] = mic[1]
        try:
            info = sd.query_devices(mic[0])
            ch = 2 if info["max_input_channels"] >= 2 else 1
            self._mic_stream = sd.InputStream(device=mic[0], samplerate=SAMPLE_RATE, channels=ch,
                                              dtype="float32", blocksize=BLOCK, latency="low",
                                              extra_settings=self._extra(), callback=self._mic_cb)
            self._mic_stream.start()
            self.status["passthrough"] = True
            log.info("soundboard: %s -> %s (+ sounds)", mic[1], cable[1])
        except Exception as exc:  # noqa: BLE001
            self.status["error"] = f"{mic[1]}: {exc}"
            log.warning("soundboard: cannot open microphone %s: %s", mic[1], exc)
            self._mic_stream = None

    def _close_mic_bus(self) -> None:
        for name in ("_mic_stream", "_cable_stream"):
            st = getattr(self, name)
            setattr(self, name, None)
            if st is not None:
                try:
                    st.abort()
                    st.close()
                except Exception:  # noqa: BLE001
                    pass
        with self._lock:
            self._jitter.clear()
            self._jitter_frames = 0
            self._primed = False
            self.voices = [v for v in self.voices if v.bus != "mic"]
        self.status["passthrough"] = False

    def _ensure_local(self) -> bool:
        with PA_LOCK:
            return self._open_local()

    def _open_local(self) -> bool:
        if self._local_stream is not None:
            return True
        sd = self._sounddevice()
        try:
            dev = self._default("output")
            self._local_stream = sd.OutputStream(device=dev, samplerate=SAMPLE_RATE, channels=CHANNELS,
                                                 dtype="float32", blocksize=BLOCK, latency="low",
                                                 extra_settings=self._extra(), callback=self._local_cb)
            self._local_stream.start()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("soundboard: cannot open the default output: %s", exc)
            self._local_stream = None
            return False

    def _close_local(self) -> None:
        st, self._local_stream = self._local_stream, None
        if st is not None:
            try:
                st.abort()
                st.close()
            except Exception:  # noqa: BLE001
                pass

    def _housekeeping(self) -> None:
        while self._started:
            time.sleep(1.0)
            with self._lock:
                busy = any(v.bus == "local" for v in self.voices)
            if busy:
                self._local_last = time.monotonic()
            elif self._local_stream is not None and time.monotonic() - self._local_last > LOCAL_IDLE_S:
                with PA_LOCK:
                    self._close_local()       # free the headset (exclusive-mode games, Bluetooth...)

    # ---------------------------------------------------------------- callbacks (audio thread)
    def _mic_cb(self, indata, frames, _time, status) -> None:
        block = indata if indata.shape[1] == 2 else np.repeat(indata, 2, axis=1)
        with self._lock:
            self._jitter.append(block.copy())
            self._jitter_frames += len(block)
            while self._jitter_frames > JITTER_MAX:
                self._jitter_frames -= len(self._jitter.popleft())

    def _take_mic(self, frames: int) -> np.ndarray | None:
        # called with the lock held
        if not self._primed:
            if self._jitter_frames < JITTER_START:
                return None
            self._primed = True
        if self._jitter_frames < frames:
            self._primed = False          # underrun: re-prime instead of crackling
            return None
        out = np.empty((frames, 2), dtype=np.float32)
        got = 0
        while got < frames:
            blk = self._jitter[0]
            take = min(frames - got, len(blk))
            out[got:got + take] = blk[:take]
            if take == len(blk):
                self._jitter.popleft()
            else:
                self._jitter[0] = blk[take:]
            got += take
        self._jitter_frames -= frames
        return out

    def _cable_cb(self, outdata, frames, _time, status) -> None:
        out = np.zeros((frames, 2), dtype=np.float32)
        with self._lock:
            mic = self._take_mic(frames) if self._mic_stream is not None else None
            if mic is not None:
                out += mic * float(self.cfg.get("mic_gain", 1.0))
            done = mix_into(out, [v for v in self.voices if v.bus == "mic"], float(self.cfg.get("volume", 0.8)))
            for v in done:
                self.voices.remove(v)
        outdata[:] = soft_clip(out)

    def _local_cb(self, outdata, frames, _time, status) -> None:
        out = np.zeros((frames, 2), dtype=np.float32)
        with self._lock:
            done = mix_into(out, [v for v in self.voices if v.bus == "local"], float(self.cfg.get("volume", 0.8)))
            for v in done:
                self.voices.remove(v)
        outdata[:] = soft_clip(out)

    # ---------------------------------------------------------------- control (any thread)
    def has_cable(self) -> bool:
        return self._cable_stream is not None

    def play(self, sid: str, target: str = "mic") -> str:
        """Start a sound; the same sound on the same target again stops it.

        Returns what happened: "playing", "stopped", "no_cable" (played on the headset instead)
        or "missing"."""
        data = self.lib.samples(sid)
        if data is None:
            return "missing"
        buses = {"mic": ["mic"], "local": ["local"], "both": ["mic", "local"]}.get(target, ["mic"])
        result = "playing"
        if "mic" in buses and not self.has_cable():
            buses = ["local"]
            result = "no_cable"
        with self._lock:
            mine = [v for v in self.voices if v.sid == sid and v.bus in buses]
            if mine:
                for v in mine:
                    self.voices.remove(v)
                # stopping a mic sound also stops its monitor copy
                self.voices = [v for v in self.voices if not (v.sid == sid and v.bus == "local" and target == "mic")]
                return "stopped"
        local_ok = False
        if "local" in buses or (buses == ["mic"] and self.cfg.get("monitor")):
            local_ok = self._ensure_local()
            if not local_ok and buses == ["local"]:
                return "missing"
        now = time.monotonic()
        with self._lock:
            for bus in buses:
                if bus == "local" and not local_ok:
                    continue
                self.voices.append(Voice(sid, data, bus, 1.0, 0, now))
            if buses == ["mic"] and self.cfg.get("monitor") and local_ok:
                self.voices.append(Voice(sid, data, "local", float(self.cfg.get("monitor_volume", 0.6)), 0, now))
        self._local_last = now
        return result

    def stop_all(self) -> None:
        with self._lock:
            self.voices = []

    def playing(self) -> dict[str, tuple[str, float]]:
        """sound id -> (bus, progress) for the widgets; the mic bus wins when both play."""
        with self._lock:
            out: dict[str, tuple[str, float]] = {}
            for v in self.voices:
                if v.sid not in out or v.bus == "mic":
                    out[v.sid] = (v.bus, v.progress)
            return out

    def set(self, key: str, value) -> None:
        self.cfg[key] = value

    def change_volume(self, step: float) -> float:
        v = max(0.0, min(1.5, float(self.cfg.get("volume", 0.8)) + step))
        self.cfg["volume"] = round(v, 3)
        return v


def _same_cable(input_name: str, cable_render_name: str) -> bool:
    a, b = input_name.lower(), cable_render_name.lower()
    for key in ("steam streaming microphone", "cable", "voicemeeter"):
        if key in a and key in b:
            return True
    return False


class NullMixer(Mixer):
    """No audio devices (tests, FREEDOCK_NO_AUDIO=1): every sound "plays" silently on the headset bus."""

    def start(self) -> None:
        self._started = True

    def devices(self, kind: str) -> list[tuple[int, str]]:
        return []

    def _ensure_local(self) -> bool:
        return True

    def restart(self, config: dict | None = None) -> None:
        if config is not None:
            self.cfg.update(config)

    def stop(self) -> None:
        self._started = False
