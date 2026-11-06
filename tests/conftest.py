"""Tests never open real audio devices (the soundboard would grab the microphone)."""
import os

os.environ["FREEDOCK_NO_AUDIO"] = "1"
