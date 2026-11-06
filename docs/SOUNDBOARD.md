# Soundboard (Sounds & DJ page)

Tap a key → your friends hear the sound **through your microphone**.
Hold the key → only **you** hear it, on your default output (headset / speakers).

```
your mic ─┐
          ├─► FreeDock mixer ─► virtual cable ─► Discord / game / Teams (input device)
 sounds ──┘
 sounds (hold) ─────────────────────────────────► your headset
```

Voice chat apps take **one** input device, so FreeDock passes your real microphone through and adds
the sounds on top. The chat app listens to the other end of a *virtual audio cable*.

## 1. A virtual cable

FreeDock picks one automatically, in this order:

| Cable | Notes |
|---|---|
| **VB-CABLE** ([vb-audio.com/Cable](https://vb-audio.com/Cable/), free / donationware) | recommended; install, reboot |
| Voicemeeter | works if you already use it |
| **Steam Streaming Microphone** | already there if Steam is installed; Steam uses it during Remote Play |

*Settings → Sounds* shows which cable is in use and what to choose in your chat app. If no cable is
found, every sound plays on your headset (a toast tells you once).

**Level.** Virtual cables often ship with an odd recording level. Steam's is −17 dB, and its slider
goes up to +30 dB, so "100 %" clips. Press **Fix the cable's level (0 dB)** once.

## 2. Your chat app

Choose the cable's *recording* side as the input device:

| Cable | Input device to pick |
|---|---|
| VB-CABLE | `CABLE Output (VB-Audio Virtual Cable)` |
| Steam | `Microphone (Steam Streaming Microphone)` |

In **Discord**: *User Settings → Voice & Video → Input Device*.

- Turn **Noise Suppression (Krisp)** off or to *Standard*, otherwise it removes music and memes.
- Echo cancellation can also eat sounds.

Your voice still works as before: FreeDock reads your Windows default microphone, or the one you
pick in *Settings → Sounds*. That only works while FreeDock runs. If FreeDock is closed, switch the
chat app back to your real microphone.

## 3. Adding sounds

- **From a link.** Use the **+** next to the sound picker (key editor) or *Settings → Sounds → Add*.
  Paste a YouTube link or any site [yt-dlp](https://github.com/yt-dlp/yt-dlp) supports, and
  optionally cut it (`from 0:12 to 0:17`). A link with `?t=83` starts there. yt-dlp is downloaded on
  first use from its official release and checksum-verified. Clips are capped at 5 minutes, from
  videos up to 30 minutes long.
- **From a file.** MP3, WAV, OGG, Opus, M4A, FLAC, WebM…
- **Command line:**
  ```
  freedock sound add "https://www.youtube.com/watch?v=…" --name "Bruh" --start 0:12 --end 0:14
  freedock sound add C:\memes\airhorn.mp3
  freedock sound list
  freedock sound remove bruh
  ```

Every sound is stored ready to play in `%APPDATA%\FreeDock\sounds` (`~/.config/freedock/sounds`).
Each one is trimmed, faded and loudness-matched, so all memes come out at a similar volume.

Only use clips you have the right to use.

## 4. Keys and knobs

A key with the action **Play a sound** has three choices:

| Field | Default | |
|---|---|---|
| Sound | – | from the library |
| Tap plays | in the microphone | or *on my headset*, or *both* |
| Hold plays | on my headset | or *microphone*, *both*, *nothing* |

Tapping a sound that is playing stops it. While it plays, the key shows a progress bar: green
**ON MIC** means your friends hear it, blue **Headset** means only you do.

The Sounds & DJ page knobs:

| Knob | Turn | Press |
|---|---|---|
| 1 | system volume | mute the system |
| 2 | microphone volume | mute the mic |
| 3 | sound volume | stop all sounds |
| 4 | previous / next track | play / pause |

**Hear it too** (key 10) also plays the microphone sounds quietly on your headset, so you hear what
your friends hear. Set its volume in *Settings → Sounds*.

## Scripts / stream tools

```
curl "http://127.0.0.1:47810/sound?id=airhorn&target=mic"
curl "http://127.0.0.1:47810/sound?stop=1"
```

## Linux

Sounds play on the default output through PortAudio (PipeWire / PulseAudio). On Linux the virtual
microphone is a null sink and a remap source, and FreeDock does not create them yet:

```
pactl load-module module-null-sink sink_name=freedock_mic sink_properties=device.description=FreeDock-Mic
pactl load-module module-remap-source master=freedock_mic.monitor source_name=freedock_mic_src \
      source_properties=device.description=FreeDock-Microphone
```

Then pick *FreeDock-Mic* as the cable in *Settings → Sounds*, and *FreeDock-Microphone* as the
input in your chat app. This route has not been tested yet.
