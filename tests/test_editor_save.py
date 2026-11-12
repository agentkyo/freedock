"""Every edit made in the editor must reach profile.json (right panel included)."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])

from freedock.engine import Engine  # noqa: E402
from freedock.gui.main_window import MainWindow  # noqa: E402
from freedock.presets import build_default_profile  # noqa: E402
from freedock.settings import Settings  # noqa: E402


def _window(tmp_path):
    prof = build_default_profile("en")
    prof.path = tmp_path / "profile.json"
    prof.save()
    win = MainWindow(Engine(prof, Settings(language="en")), prof, Settings(language="en"))
    win.tray.hide()
    return win, prof


def test_inspector_edit_is_saved(tmp_path):
    win, prof = _window(tmp_path)
    slot = win.page.keys[0]
    win.inspector._edit(slot, "label", "Saved label")
    assert win._save_timer.isActive() and win.save_btn.text().endswith("•")
    win.save_now()
    saved = json.loads(prof.path.read_text(encoding="utf-8"))
    page = next(p for p in saved["pages"] if p["id"] == win.page.id)
    assert page["keys"][0]["label"] == "Saved label"
    assert not win._save_timer.isActive() and "✓" in win.save_btn.text()


def test_hiding_to_tray_flushes_pending_edit(tmp_path):
    win, prof = _window(tmp_path)
    win.inspector._edit(win.page.keys[1], "label", "Before hide")
    win.close()                                  # tray available or not, nothing may stay pending
    assert "Before hide" in prof.path.read_text(encoding="utf-8")
