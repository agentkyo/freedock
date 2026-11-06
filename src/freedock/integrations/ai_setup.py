"""Install / remove FreeDock's bridges into Claude Code, Codex and OpenCode.

Every change is reversible, keeps a timestamped backup next to the file, and preserves what the
user already had (their hooks, Codex's own ``notify`` program, a custom status line).
"""
from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from . import ai

log = logging.getLogger(__name__)


def _backup(path: Path) -> None:
    if path.exists():
        shutil.copy2(path, path.with_name(f"{path.name}.freedock-{time.strftime('%Y%m%d-%H%M%S')}.bak"))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- Claude Code
def claude_settings_path() -> Path:
    return ai.claude_home() / "settings.json"


def _claude_load() -> dict:
    p = claude_settings_path()
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8") or "{}")   # invalid JSON raises: never overwrite it


def claude_installed() -> bool:
    try:
        hooks = _claude_load().get("hooks") or {}
    except (OSError, ValueError):
        return False
    return any(ai._is_ours(h) for groups in hooks.values() if isinstance(groups, list)
               for g in groups if isinstance(g, dict) for h in g.get("hooks", []))


def claude_install(command: list[str], statusline: bool = True) -> dict | None:
    """Add the hooks (and the status line that captures plan %). Returns the displaced status line."""
    settings = _claude_load()
    new = ai.claude_hooks_patch(settings, command)
    previous = None
    if statusline:
        new, previous = ai.claude_statusline_patch(new, command)
    _backup(claude_settings_path())
    _write(claude_settings_path(), json.dumps(new, ensure_ascii=False, indent=2) + "\n")
    return previous


def claude_remove(previous_statusline: dict | None = None) -> None:
    settings = _claude_load()
    new = ai.claude_statusline_remove(ai.claude_hooks_remove(settings), previous_statusline)
    if new != settings:
        _backup(claude_settings_path())
        _write(claude_settings_path(), json.dumps(new, ensure_ascii=False, indent=2) + "\n")


# --------------------------------------------------------------------------- Codex
def codex_config_path() -> Path:
    return ai.codex_home() / "config.toml"


def codex_installed() -> bool:
    try:
        text = codex_config_path().read_text(encoding="utf-8")
    except OSError:
        return False
    return ai.CODEX_MARKER in text or "freedock" in text.lower().split("[", 1)[0]


def codex_install(command: list[str]) -> None:
    p = codex_config_path()
    text = p.read_text(encoding="utf-8") if p.exists() else ""
    new = ai.codex_notify_patch(text, command)
    if new != text:
        _backup(p)
        _write(p, new)


def codex_remove() -> None:
    p = codex_config_path()
    if not p.exists():
        return
    text = p.read_text(encoding="utf-8")
    new = ai.codex_notify_remove(text)
    if new != text:
        _backup(p)
        _write(p, new)


def codex_previous() -> list[str] | None:
    try:
        return ai.codex_previous_notify(codex_config_path().read_text(encoding="utf-8"))
    except OSError:
        return None


# --------------------------------------------------------------------------- OpenCode
def opencode_installed() -> bool:
    return ai.opencode_plugin_path().exists()


def opencode_install(command: list[str]) -> None:
    _write(ai.opencode_plugin_path(), ai.opencode_plugin_source(command))


def opencode_remove() -> None:
    p = ai.opencode_plugin_path()
    if p.exists():
        p.unlink()
