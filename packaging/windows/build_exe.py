"""Build dist/FreeDock/FreeDock.exe with PyInstaller (one folder, no console window).

    .venv\\Scripts\\python.exe -m pip install pyinstaller
    .venv\\Scripts\\python.exe packaging\\windows\\build_exe.py

The executable is not code-signed. Windows SmartScreen shows a warning on first launch, and
with Smart App Control turned on Windows refuses to run it; use install.ps1 (signed pythonw.exe)
on such machines.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BUILD = ROOT / "build" / "exe"


def make_icon(path: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path.insert(0, str(ROOT / "src"))
    from PySide6.QtGui import QGuiApplication
    QGuiApplication([])
    from freedock.render.painter import icon_image
    img = icon_image("layout-grid", "#5b8cff", 256, 2.4)
    if img is None or not img.save(str(path), "ICO"):
        raise SystemExit("could not write the icon")


def main() -> int:
    BUILD.mkdir(parents=True, exist_ok=True)
    icon = BUILD / "freedock.ico"
    make_icon(icon)
    entry = BUILD / "freedock_entry.py"
    entry.write_text("import sys\nfrom freedock.cli import main\nsys.exit(main())\n", encoding="utf-8")
    sep = os.pathsep
    src = ROOT / "src" / "freedock"
    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed",
           "--name", "FreeDock", "--icon", str(icon),
           "--distpath", str(ROOT / "dist"), "--workpath", str(BUILD / "work"), "--specpath", str(BUILD),
           "--paths", str(ROOT / "src"),
           "--collect-submodules", "freedock",
           "--add-data", f"{src / 'assets'}{sep}freedock/assets",
           "--add-data", f"{src / 'locales'}{sep}freedock/locales",
           "--hidden-import", "comtypes.stream",
           # soundboard: PortAudio DLL + Qt Multimedia (its bundled FFmpeg decodes MP3/M4A/Opus)
           "--collect-all", "_sounddevice_data",
           "--hidden-import", "PySide6.QtMultimedia",
           # Qt modules FreeDock never touches: ~150 MB less
           *[a for m in ("QtWebEngineCore", "QtWebEngineWidgets", "Qt3DCore", "QtQuick", "QtQml",
                         "QtCharts", "QtDataVisualization", "QtPdf", "QtSql", "QtTest")
             for a in ("--exclude-module", f"PySide6.{m}")],
           str(entry)]
    shutil.rmtree(ROOT / "dist" / "FreeDock", ignore_errors=True)
    subprocess.check_call(cmd, cwd=ROOT)
    print(f"built {ROOT / 'dist' / 'FreeDock' / 'FreeDock.exe'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
