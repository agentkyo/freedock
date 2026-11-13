"""App settings (language, autostart, integrations) - separate from the profile so profiles can be shared."""
from __future__ import annotations

import json
import locale
from dataclasses import asdict, dataclass, field
from typing import Any

from .profile import config_dir


def system_language() -> str:
    try:
        lang = (locale.getlocale()[0] or "").lower()
    except ValueError:
        lang = ""
    if lang.startswith(("pt", "portuguese")):
        return "pt"
    if lang.startswith(("es", "spanish")):
        return "es"
    return "en"


@dataclass
class Settings:
    language: str = ""
    device_serial: str | None = None
    start_minimized: bool = False
    weather: dict[str, Any] = field(default_factory=lambda: {"lat": None, "lon": None, "city": ""})
    obs: dict[str, Any] = field(default_factory=lambda: {"host": "localhost", "port": 4455, "password": ""})
    assetto_controls: str = ""         # override path to Assetto Corsa cfg/controls.ini
    simconnect_dll: str = ""           # override path to SimConnect.dll
    notify: dict[str, Any] = field(default_factory=lambda: {"banner": True, "lights": True, "http_port": 47810})
    ai: dict[str, Any] = field(default_factory=dict)   # claude_statusline_previous...
    lighting: dict[str, Any] = field(default_factory=lambda: {"strip": True, "strip_count": 71, "chroma": True})
    # game lighting (integrations/game_lights.py): strip = events | theme | off; native_chroma = theme ids
    # whose own Chroma effects keep the keyboard; off = theme ids never shown
    game_lights: dict[str, Any] = field(default_factory=lambda: {
        "enabled": True, "strip": "events", "native_chroma": [], "off": [], "idle": "screen"})
    cs2: dict[str, Any] = field(default_factory=lambda: {"port": 47812, "token": ""})
    soundboard: dict[str, Any] = field(default_factory=lambda: {
        "cable": "", "mic": "", "passthrough": True, "monitor": False, "monitor_volume": 0.6,
        "volume": 0.8, "mic_gain": 1.0})

    @classmethod
    def load(cls) -> "Settings":
        path = config_dir() / "settings.json"
        s = cls()
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
            except (OSError, ValueError):
                pass
        if not s.language:
            s.language = system_language()
        return s

    def save(self) -> None:
        path = config_dir() / "settings.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
