"""Test isolation.

* Tests never open real audio devices (the soundboard would grab the microphone).
* Tests never touch the user's real config: FreeDock's settings.json / profile.json live in a
  throw-away folder (an Engine built with default Settings would otherwise overwrite them).
* Tests never write into game folders or open game ports (CS2's game state integration).
"""
import os
import tempfile

os.environ["FREEDOCK_NO_AUDIO"] = "1"
os.environ["FREEDOCK_NO_GAMES"] = "1"
_CONFIG = tempfile.mkdtemp(prefix="freedock-tests-")
os.environ["APPDATA"] = _CONFIG
os.environ["XDG_CONFIG_HOME"] = _CONFIG
