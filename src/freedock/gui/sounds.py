"""Soundboard UI: add sounds from a link or a file, pick them in the editor, set up the virtual mic."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import webbrowser

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout,
                               QHBoxLayout, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QProgressBar, QPushButton, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import APP_NAME
from ..i18n import tr
from ..render.painter import icon_image
from ..soundboard.library import display_name
from .editors import section

log = logging.getLogger(__name__)

VB_CABLE_URL = "https://vb-audio.com/Cable/"
AUDIO_FILTER = "Audio (*.mp3 *.wav *.ogg *.opus *.m4a *.aac *.flac *.webm *.mp4 *.wma)"


def _icon(name: str, color: str = "#e6e9f0") -> QIcon:
    img = icon_image(name or "audio-lines", color, 18)
    return QIcon(QPixmap.fromImage(img)) if img is not None else QIcon()


class _Relay(QObject):
    progress = Signal(float, str)
    done = Signal(object, str)          # (Sound | None, error)


class SoundDialog(QDialog):
    """Add one sound: a YouTube (or other) link or a local file, optionally trimmed."""

    def __init__(self, parent, url: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("sound.add_title"))
        self.resize(520, 300)
        self.sound = None
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        link = QWidget()
        fl = QFormLayout(link)
        self.url = QLineEdit(url)
        self.url.setPlaceholderText("https://www.youtube.com/watch?v=…")
        fl.addRow(tr("sound.link"), self.url)
        hint = QLabel(tr("sound.link_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        fl.addRow(hint)
        self.tabs.addTab(link, _icon("link"), tr("sound.from_link"))
        file = QWidget()
        ff = QFormLayout(file)
        row = QHBoxLayout()
        self.path = QLineEdit()
        browse = QPushButton("…")
        browse.setFixedWidth(34)
        browse.clicked.connect(self._browse)
        row.addWidget(self.path, 1)
        row.addWidget(browse)
        ff.addRow(tr("sound.file"), row)
        self.tabs.addTab(file, _icon("file-audio"), tr("sound.from_file"))
        lay.addWidget(self.tabs)

        form = QFormLayout()
        self.name = QLineEdit()
        self.name.setPlaceholderText(tr("sound.name_auto"))
        form.addRow(tr("sound.name"), self.name)
        times = QHBoxLayout()
        self.start = QLineEdit()
        self.start.setPlaceholderText("0:00")
        self.end = QLineEdit()
        self.end.setPlaceholderText(tr("sound.until_end"))
        times.addWidget(QLabel(tr("sound.start")))
        times.addWidget(self.start)
        times.addWidget(QLabel(tr("sound.end")))
        times.addWidget(self.end)
        form.addRow(tr("sound.cut"), times)
        lay.addLayout(form)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.hide()
        self.status = QLabel("")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
        lay.addWidget(self.bar)
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("sound.add"))
        self.buttons.accepted.connect(self._go)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)
        self._relay = _Relay()
        self._relay.progress.connect(self._on_progress)
        self._relay.done.connect(self._on_done)
        self._busy = False
        if url:
            self.tabs.setCurrentIndex(0)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("sound.file"), "", AUDIO_FILTER)
        if path:
            self.path.setText(path)
            if not self.name.text():
                self.name.setText(os.path.splitext(os.path.basename(path))[0])
            self.tabs.setCurrentIndex(1)

    def _go(self) -> None:
        if self._busy:
            return
        from ..soundboard.library import library, parse_time
        try:
            start, end = parse_time(self.start.text()), parse_time(self.end.text())
        except ValueError as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        from_link = self.tabs.currentIndex() == 0
        src = self.url.text().strip() if from_link else self.path.text().strip()
        if not src:
            return
        self._busy = True
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.bar.show()
        self.bar.setRange(0, 0)
        self.status.setText(tr("sound.working"))
        name = self.name.text().strip()
        relay = self._relay

        def work():
            try:
                lib = library()
                if from_link:
                    from ..soundboard.ytdl import add_from_url
                    snd = add_from_url(lib, src, name, start, end,
                                       progress=lambda f, msg: relay.progress.emit(-1.0 if f is None else f, msg))
                else:
                    snd = lib.add_file(src, name, start, end)
                relay.done.emit(snd, "")
            except Exception as exc:  # noqa: BLE001 - shown to the user
                log.warning("adding sound failed: %s", exc)
                relay.done.emit(None, str(exc) or exc.__class__.__name__)

        threading.Thread(target=work, name="sound-add", daemon=True).start()

    def _on_progress(self, frac: float, msg: str) -> None:
        if frac < 0:
            self.bar.setRange(0, 0)
        else:
            self.bar.setRange(0, 1000)
            self.bar.setValue(int(frac * 1000))
        if msg:
            self.status.setText(msg)

    def _on_done(self, sound, error: str) -> None:
        self._busy = False
        self.bar.hide()
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        if sound is None:
            self.status.setText("⚠ " + error)
            return
        self.sound = sound
        self.accept()

    def reject(self) -> None:
        if self._busy:
            return                      # let the download finish; closing mid-way would orphan it
        super().reject()


class SoundPicker(QWidget):
    """Combo of library sounds + a "new sound" button. Used by the editor for ``sound`` fields."""
    changed = Signal(str)

    def __init__(self, value: str, parent=None):
        super().__init__(parent)
        h = QHBoxLayout(self)
        h.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(150)
        add = QPushButton("+")
        add.setFixedWidth(34)
        add.setToolTip(tr("sound.add_title"))
        play = QPushButton("▶")
        play.setFixedWidth(34)
        play.setToolTip(tr("sound.preview"))
        h.addWidget(self.combo, 1)
        h.addWidget(play)
        h.addWidget(add)
        self._fill(value)
        self.combo.currentIndexChanged.connect(lambda _: self.changed.emit(self.combo.currentData() or ""))
        add.clicked.connect(self._add)
        play.clicked.connect(self._preview)

    def _fill(self, select: str | None) -> None:
        from ..soundboard.library import library
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItem(tr("sound.none_selected"), "")
        for s in library().list():
            self.combo.addItem(_icon(s.icon, s.color or "#e6e9f0"), f"{display_name(s)}  ·  {s.duration:.1f}s", s.id)
        i = self.combo.findData(select or "")
        if i < 0 and select:
            self.combo.addItem(f"? {select}", select)
            i = self.combo.count() - 1
        self.combo.setCurrentIndex(max(0, i))
        self.combo.blockSignals(False)

    def _add(self) -> None:
        dlg = SoundDialog(self.window())
        if dlg.exec() and dlg.sound is not None:
            self._fill(dlg.sound.id)
            self.changed.emit(dlg.sound.id)

    def _preview(self) -> None:
        sid = self.combo.currentData()
        engine = getattr(self.window(), "engine", None)
        if sid and engine is not None:
            engine.sound_play(sid, "local")


class SoundboardTab(QWidget):
    """Settings → Sounds: the virtual microphone and the sound library."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        cfg = dict(win.settings.soundboard or {})
        f = QFormLayout(self)
        self.state = QLabel()
        self.state.setWordWrap(True)
        self.state.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        f.addRow(self.state)

        f.addRow(section(tr("sound.virtual_mic")))
        self.cable = QComboBox()
        self.mic = QComboBox()
        self.cable.addItem(tr("sound.auto"), "")
        self.mic.addItem(tr("sound.default_mic"), "")
        try:
            m = win.engine.soundboard()
            for _i, name in m.devices("output"):
                self.cable.addItem(name, name)
            for _i, name in m.devices("input"):
                self.mic.addItem(name, name)
        except Exception as exc:  # noqa: BLE001 - no audio stack
            self.state.setText("⚠ " + str(exc))
        self.cable.setCurrentIndex(max(0, self.cable.findData(cfg.get("cable") or "")))
        self.mic.setCurrentIndex(max(0, self.mic.findData(cfg.get("mic") or "")))
        f.addRow(tr("sound.cable"), self.cable)
        f.addRow(tr("sound.real_mic"), self.mic)
        self.passthrough = QCheckBox(tr("sound.passthrough"))
        self.passthrough.setChecked(bool(cfg.get("passthrough", True)))
        f.addRow("", self.passthrough)
        self.monitor = QCheckBox(tr("sound.monitor"))
        self.monitor.setChecked(bool(cfg.get("monitor", False)))
        self.monitor_vol = QSpinBox()
        self.monitor_vol.setRange(5, 100)
        self.monitor_vol.setSuffix(" %")
        self.monitor_vol.setValue(int(float(cfg.get("monitor_volume", 0.6)) * 100))
        mrow = QHBoxLayout()
        mrow.addWidget(self.monitor, 1)
        mrow.addWidget(self.monitor_vol)
        f.addRow("", mrow)
        self.volume = QSpinBox()
        self.volume.setRange(0, 150)
        self.volume.setSuffix(" %")
        self.volume.setValue(int(float(cfg.get("volume", 0.8)) * 100))
        f.addRow(tr("sound.volume"), self.volume)
        self.mic_gain = QSpinBox()
        self.mic_gain.setRange(10, 300)
        self.mic_gain.setSuffix(" %")
        self.mic_gain.setValue(int(float(cfg.get("mic_gain", 1.0)) * 100))
        f.addRow(tr("sound.mic_gain"), self.mic_gain)
        brow = QHBoxLayout()
        level = QPushButton(tr("sound.fix_level"))
        level.setToolTip(tr("sound.fix_level_hint"))
        level.clicked.connect(self._fix_level)
        vb = QPushButton(tr("sound.get_cable"))
        vb.clicked.connect(lambda: webbrowser.open(VB_CABLE_URL))
        brow.addWidget(level)
        brow.addWidget(vb)
        f.addRow("", brow)

        f.addRow(section(tr("sound.library")))
        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        f.addRow(self.list)
        lrow = QHBoxLayout()
        for text, fn in ((tr("sound.add"), self._add), ("▶", self._play), (tr("sound.rename"), self._rename),
                         (tr("sound.remove"), self._remove), (tr("sound.folder"), self._folder)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            lrow.addWidget(b)
        f.addRow(lrow)
        self._fill()
        self._refresh_state()

    # ---------------------------------------------------------------- state
    def _refresh_state(self) -> None:
        try:
            m = self.win.engine.soundboard()
        except Exception as exc:  # noqa: BLE001
            self.state.setText("⚠ " + str(exc))
            return
        st = m.status
        if st.get("cable"):
            cap = m.cable_capture_name(st["cable"])
            lines = [f"<b style='color:#22c55e'>● {tr('sound.state_ready')}</b>",
                     tr("sound.state_route", mic=st.get("mic") or "—", cable=st["cable"]),
                     tr("sound.state_chat", device=cap)]
            if not st.get("passthrough") and self.passthrough.isChecked():
                lines.append("⚠ " + tr("sound.state_no_mic"))
        else:
            lines = [f"<b style='color:#f59e0b'>● {tr('sound.state_no_cable')}</b>", tr("sound.state_how")]
        if st.get("error"):
            lines.append("⚠ " + st["error"])
        self.state.setText("<br>".join(lines))

    def _fix_level(self) -> None:
        from ..platform import os_backend as osb
        m = self.win.engine.soundboard()
        name = m.status.get("cable") or ""
        part = "Steam Streaming Microphone" if "steam" in name.lower() else "CABLE Output" if "cable" in name.lower() \
            else name
        if not part:
            QMessageBox.information(self, APP_NAME, tr("sound.state_no_cable"))
            return
        done = osb.set_capture_level(part, 0.0)
        QMessageBox.information(self, APP_NAME, tr("sound.level_done", n=len(done)) if done else tr("sound.level_none"))

    # ---------------------------------------------------------------- library
    def _fill(self, select: str | None = None) -> None:
        from ..soundboard.library import library
        self.list.clear()
        for s in library().list():
            it = QListWidgetItem(_icon(s.icon, s.color or "#e6e9f0"), f"{display_name(s)}   ·   {s.duration:.1f}s"
                                 + ("" if not s.source or s.builtin else f"   ·   {s.source[:60]}"))
            it.setData(Qt.ItemDataRole.UserRole, s.id)
            self.list.addItem(it)
            if s.id == select:
                self.list.setCurrentItem(it)

    def _current(self) -> str | None:
        it = self.list.currentItem()
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _add(self) -> None:
        dlg = SoundDialog(self.window())
        if dlg.exec() and dlg.sound is not None:
            self._fill(dlg.sound.id)

    def _play(self) -> None:
        if sid := self._current():
            self.win.engine.sound_play(sid, "local")

    def _rename(self) -> None:
        from ..soundboard.library import library
        sid = self._current()
        snd = library().get(sid)
        if snd is None:
            return
        name, ok = QInputDialog.getText(self, APP_NAME, tr("sound.name"), text=display_name(snd))
        if ok and name.strip():
            library().update(sid, name=name.strip())
            self._fill(sid)

    def _remove(self) -> None:
        from ..soundboard.library import library
        sid = self._current()
        snd = library().get(sid)
        if snd is None:
            return
        if QMessageBox.question(self, APP_NAME, tr("sound.remove_confirm", name=display_name(snd))) == \
                QMessageBox.StandardButton.Yes:
            library().remove(sid)
            self._fill()

    def _folder(self) -> None:
        from ..soundboard.library import library
        root = library().root
        root.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(str(root))  # noqa: S606
        else:
            subprocess.Popen(["xdg-open", str(root)])

    # ---------------------------------------------------------------- save
    def apply(self) -> None:
        s = self.win.settings
        cfg = {**(s.soundboard or {}), "cable": self.cable.currentData() or "", "mic": self.mic.currentData() or "",
               "passthrough": self.passthrough.isChecked(), "monitor": self.monitor.isChecked(),
               "monitor_volume": self.monitor_vol.value() / 100, "volume": self.volume.value() / 100,
               "mic_gain": self.mic_gain.value() / 100}
        changed_devices = any(cfg.get(k) != (s.soundboard or {}).get(k) for k in ("cable", "mic", "passthrough"))
        s.soundboard = cfg
        m = self.win.engine._mixer
        if m is not None:
            m.cfg.update(cfg)
            if changed_devices:
                m.restart()
