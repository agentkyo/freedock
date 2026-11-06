# Third-party assets

| Asset | Where | License |
|---|---|---|
| [Lucide](https://lucide.dev) icons v0.460.0 | `src/freedock/assets/icons/` | ISC (`LICENSE.lucide.txt`) |
| [Inter](https://rsms.me/inter/) | `src/freedock/assets/fonts/Inter.ttf` | SIL Open Font License 1.1 (`OFL-Inter.txt`) |
| [JetBrains Mono](https://www.jetbrains.com/lp/mono/) | `src/freedock/assets/fonts/JetBrainsMono.ttf` | SIL Open Font License 1.1 (`OFL-JetBrainsMono.txt`) |

Runtime dependencies (installed by pip): PySide6 (LGPL-3.0), hidapi (BSD/GPL dual), psutil (BSD-3),
pycaw (MIT, Windows), comtypes (MIT, Windows), python-evdev (BSD-3, Linux).

Not bundled, used when present: the [scs-sdk-plugin](https://github.com/RenCloud/scs-sdk-plugin) telemetry plugin for Euro Truck / American Truck Simulator (its shared-memory layout is read, no code is copied), BeamNG.drive's built-in OutGauge, the Razer Chroma SDK REST API and the [Ambibox LED](https://github.com/agentkyo/ambibox-led-ambilight) control channel (MIT, same author; `integrations/lighting.py` carries a small copy of its packet builders).

MiraBox, StreamDock, Assetto Corsa, Microsoft Flight Simulator, Euro Truck Simulator, American Truck Simulator, BeamNG.drive, Razer Chroma, Claude, Codex and OpenCode are trademarks of their owners.
FreeDock is an independent project and is not affiliated with any of them.

## Soundboard

| Component | License | How it is used |
|---|---|---|
| [NumPy](https://numpy.org) | BSD-3-Clause | mixing audio buffers (bundled) |
| [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) | MIT | audio streams (bundled) |
| [PortAudio](https://www.portaudio.com) | MIT | audio I/O library behind sounddevice (bundled DLL) |
| Qt Multimedia + FFmpeg (part of PySide6) | LGPLv3 | decoding MP3/M4A/Opus/OGG/FLAC (bundled with PySide6) |
| [yt-dlp](https://github.com/yt-dlp/yt-dlp) | Unlicense | **not bundled**: downloaded from its official GitHub release on first use, checksum-verified |

The built-in sounds are synthesised by FreeDock's own code (`soundboard/synth.py`) and are MIT
licensed like the rest of the project.

