"""Tiny i18n: JSON catalogues in locales/, English as the fallback for missing keys."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

LOCALES = Path(__file__).resolve().parent / "locales"
LANGUAGES = {"en": "English", "pt": "Português (Brasil)", "es": "Español"}
_lang = "en"


@lru_cache(maxsize=None)
def _catalog(lang: str) -> dict[str, str]:
    path = LOCALES / f"{lang}.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def set_language(lang: str) -> None:
    global _lang
    _lang = lang if lang in LANGUAGES else "en"


def language() -> str:
    return _lang


def tr(key: str, **fmt) -> str:
    text = _catalog(_lang).get(key) or _catalog("en").get(key) or key
    return text.format(**fmt) if fmt else text
