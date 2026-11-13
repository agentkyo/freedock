"""Test isolation.

* Tests never open real audio devices (the soundboard would grab the microphone).
* Tests never touch the user's real config: FreeDock's settings.json / profile.json live in a
  throw-away folder (an Engine built with default Settings would otherwise overwrite them).
* Tests never write into game folders or open game ports (CS2's game state integration).
* Tests never capture the screen (keyboard screen sync).
"""
import os
import tempfile

os.environ["FREEDOCK_NO_AUDIO"] = "1"
os.environ["FREEDOCK_NO_GAMES"] = "1"
os.environ["FREEDOCK_NO_SCREEN"] = "1"   # never sample the real screen or take the real keyboard
_CONFIG = tempfile.mkdtemp(prefix="freedock-tests-")
os.environ["APPDATA"] = _CONFIG
os.environ["XDG_CONFIG_HOME"] = _CONFIG
