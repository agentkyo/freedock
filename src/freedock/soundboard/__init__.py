"""Soundboard: short sounds ("memes") played into the microphone or the headset.

* :mod:`.library` - the sounds on disk (``<config>/sounds``) and their index
* :mod:`.decode`  - any audio file -> 48 kHz stereo samples (Qt Multimedia's bundled FFmpeg)
* :mod:`.ytdl`    - fetching the audio of a YouTube (or any yt-dlp supported) link
* :mod:`.synth`   - the built-in sounds, synthesised so nothing copyrighted ships with FreeDock
* :mod:`.mixer`   - real-time playback: the *mic* bus (your microphone + sounds, sent to a virtual
  cable that voice chat apps use as their input) and the *local* bus (your default output)
"""
SAMPLE_RATE = 48000
CHANNELS = 2
