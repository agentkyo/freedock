"""Picks the OS backend. Both expose the same functions (see windows.py / linux.py)."""
import sys

if sys.platform == "win32":
    from . import windows as os_backend
    NAME = "windows"
elif sys.platform.startswith("linux"):
    from . import linux as os_backend
    NAME = "linux"
else:  # pragma: no cover - macOS is not supported yet
    raise ImportError("FreeDock supports Windows and Linux")

from .keys import parse_combo, pretty  # noqa: E402

__all__ = ["os_backend", "NAME", "parse_combo", "pretty"]
