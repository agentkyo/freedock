"""FreeDock main window: pages on the left, the live N4 in the middle, the inspector on the right."""
from __future__ import annotations

import copy
import json
import logging
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QIcon, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMainWindow, QMenu, QMessageBox, QPushButton, QSlider, QSpinBox, QSplitter,
                               QSystemTrayIcon, QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import APP_NAME, __version__
from ..engine import Engine
from ..i18n import LANGUAGES, set_language, tr
from ..profile import Page, Profile, Slot
from ..render.painter import icon_image, load_fonts
from ..settings import Settings
from .device_view import DeviceView
from .editors import ColorButton, IconPicker, section
from .inspector import Inspector
from .theme import STYLE

log = logging.getLogger(__name__)


def app_icon() -> QIcon:
    img = icon_image("layout-grid", "#5b8cff", 64, 2.4)
    return QIcon(QPixmap.fromImage(img)) if img else QIcon()


# --------------------------------------------------------------------------- autostart
def autostart_enabled() -> bool:
    if sys.platform == "win32":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run") as k:
                winreg.QueryValueEx(k, APP_NAME)
                return True
        except OSError:
            return False
    return (Path.home() / ".config" / "autostart" / "freedock.desktop").is_file()


def set_autostart(on: bool) -> None:
    exe = Path(sys.executable)
    pyw = exe.with_name("pythonw.exe") if sys.platform == "win32" and exe.with_name("pythonw.exe").exists() else exe
    if sys.platform == "win32":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                            winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, APP_NAME, 0, winreg.REG_SZ, f'"{pyw}" -m freedock --minimized')
            else:
                try:
                    winreg.DeleteValue(k, APP_NAME)
                except OSError:
                    pass
        return
    path = Path.home() / ".config" / "autostart" / "freedock.desktop"
    if on:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"[Desktop Entry]\nType=Application\nName=FreeDock\nExec={exe} -m freedock --minimized\n"
                        "Icon=input-keyboard\nX-GNOME-Autostart-enabled=true\n", encoding="utf-8")
    elif path.exists():
        path.unlink()


# --------------------------------------------------------------------------- settings dialog
class SettingsDialog(QDialog):
    def __init__(self, win: "MainWindow"):
        super().__init__(win)
        self.win = win
        s, prof = win.settings, win.profile
        self.setWindowTitle(tr("ui.settings"))
        self.resize(520, 520)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        general = QWidget()
        f = QFormLayout(general)
        self.lang = QComboBox()
        for code, name in LANGUAGES.items():
            self.lang.addItem(name, code)
        self.lang.setCurrentIndex(max(0, self.lang.findData(s.language)))
        f.addRow(tr("ui.language"), self.lang)
        self.autostart = QCheckBox(tr("ui.autostart"))
        self.autostart.setChecked(autostart_enabled())
        f.addRow("", self.autostart)
        self.minimized = QCheckBox(tr("ui.start_minimized"))
        self.minimized.setChecked(s.start_minimized)
        f.addRow("", self.minimized)
        self.anim = QCheckBox(tr("ui.animations"))
        self.anim.setChecked(prof.animations)
        f.addRow("", self.anim)
        self.idle = QSpinBox()
        self.idle.setRange(0, 240)
        self.idle.setSuffix(" min")
        self.idle.setValue(prof.idle_minutes)
        f.addRow(tr("ui.idle_after"), self.idle)
        self.idle_mode = QComboBox()
        for v in ("clock", "aurora", "off"):
            self.idle_mode.addItem(tr(f"idle.{v}"), v)
        self.idle_mode.setCurrentIndex(max(0, self.idle_mode.findData(prof.idle_mode)))
        f.addRow(tr("ui.idle_mode"), self.idle_mode)
        tabs.addTab(general, tr("ui.general"))

        weather = QWidget()
        f2 = QFormLayout(weather)
        self.city = QLineEdit(s.weather.get("city") or "")
        find = QPushButton(tr("ui.find_city"))
        row = QHBoxLayout()
        row.addWidget(self.city, 1)
        row.addWidget(find)
        f2.addRow(tr("ui.city"), row)
        self.coords = QLabel(self._coords_text(s.weather.get("lat"), s.weather.get("lon")))
        self.coords.setObjectName("muted")
        f2.addRow("", self.coords)
        self._lat, self._lon = s.weather.get("lat"), s.weather.get("lon")
        find.clicked.connect(self._geocode)
        tabs.addTab(weather, tr("widget.weather"))

        integ = QWidget()
        f3 = QFormLayout(integ)
        f3.addRow(section("OBS Studio"))
        self.obs_host = QLineEdit(str(s.obs.get("host", "localhost")))
        self.obs_port = QSpinBox()
        self.obs_port.setRange(1, 65535)
        self.obs_port.setValue(int(s.obs.get("port", 4455)))
        self.obs_pass = QLineEdit(s.obs.get("password", ""))
        self.obs_pass.setEchoMode(QLineEdit.EchoMode.Password)
        f3.addRow("Host", self.obs_host)
        f3.addRow("Port", self.obs_port)
        f3.addRow(tr("ui.password"), self.obs_pass)
        f3.addRow(section("Assetto Corsa"))
        self.ac_path = QLineEdit(s.assetto_controls)
        self.ac_path.setPlaceholderText(tr("ui.auto_detect"))
        f3.addRow("controls.ini", self.ac_path)
        f3.addRow(section("Flight Simulator"))
        self.sc_path = QLineEdit(s.simconnect_dll)
        self.sc_path.setPlaceholderText(tr("ui.auto_detect"))
        f3.addRow("SimConnect.dll", self.sc_path)
        tabs.addTab(integ, tr("ui.integrations"))

        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    @staticmethod
    def _coords_text(lat, lon) -> str:
        return f"{lat:.3f}, {lon:.3f}" if lat is not None and lon is not None else tr("ui.no_location")

    def _geocode(self) -> None:
        q = self.city.text().strip()
        if not q:
            return
        url = "https://geocoding-api.open-meteo.com/v1/search?count=1&name=" + urllib.parse.quote(q)
        try:
            with urllib.request.urlopen(url, timeout=8) as r:  # noqa: S310
                res = (json.load(r).get("results") or [None])[0]
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        if not res:
            QMessageBox.information(self, APP_NAME, tr("ui.city_not_found"))
            return
        self._lat, self._lon = res["latitude"], res["longitude"]
        self.city.setText(f"{res['name']}")
        self.coords.setText(self._coords_text(self._lat, self._lon) + f" · {res.get('country', '')}")

    def apply(self) -> bool:
        s, prof = self.win.settings, self.win.profile
        lang_changed = self.lang.currentData() != s.language
        s.language = self.lang.currentData()
        s.start_minimized = self.minimized.isChecked()
        s.weather = {"city": self.city.text().strip(), "lat": self._lat, "lon": self._lon}
        s.obs = {"host": self.obs_host.text().strip(), "port": self.obs_port.value(), "password": self.obs_pass.text()}
        s.assetto_controls = self.ac_path.text().strip()
        s.simconnect_dll = self.sc_path.text().strip()
        s.save()
        prof.animations = self.anim.isChecked()
        prof.idle_minutes = self.idle.value()
        prof.idle_mode = self.idle_mode.currentData()
        try:
            set_autostart(self.autostart.isChecked())
        except OSError as exc:
            log.warning("autostart: %s", exc)
        return lang_changed


# --------------------------------------------------------------------------- page panel
class PagePanel(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.setObjectName("panel")
        self.setMinimumWidth(300)
        self.setMaximumWidth(360)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.addWidget(section(tr("ui.pages")))
        self.list = QListWidget()
        self.list.setIconSize(QSize(18, 18))
        self.list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.list.model().rowsMoved.connect(self._reordered)
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        for text, tip, fn in (("+", "ui.add_page", self._add), ("⧉", "ui.duplicate_page", self._dup),
                              ("−", "ui.delete_page", self._delete)):
            b = QPushButton(text)
            b.setToolTip(tr(tip))
            b.clicked.connect(fn)
            row.addWidget(b)
        lay.addLayout(row)

        lay.addWidget(section(tr("ui.page_settings")))
        form = QFormLayout()
        self.name = QLineEdit()
        self.name.editingFinished.connect(self._rename)
        form.addRow(tr("ui.name"), self.name)
        icon_row = QHBoxLayout()
        self.icon_btn = QPushButton()
        self.icon_btn.clicked.connect(self._pick_icon)
        self.accent = ColorButton()
        self.accent.changed.connect(self._accent)
        icon_row.addWidget(self.icon_btn, 1)
        icon_row.addWidget(self.accent)
        form.addRow(tr("ui.icon"), icon_row)
        self.platform = QComboBox()
        for v, label in ((None, "ui.platform_all"), ("windows", "Windows"), ("linux", "Linux")):
            self.platform.addItem(tr(label), v)
        self.platform.currentIndexChanged.connect(self._platform)
        form.addRow(tr("ui.platform"), self.platform)
        self.shift = QCheckBox(tr("ui.shift_lights"))
        self.shift.toggled.connect(self._shift)
        form.addRow("", self.shift)
        self.start = QCheckBox(tr("ui.start_page"))
        self.start.toggled.connect(self._start)
        form.addRow("", self.start)
        lay.addLayout(form)

        lay.addWidget(section(tr("ui.auto_switch")))
        hint = QLabel(tr("ui.auto_switch_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        self.rules = QTableWidget(0, 2)
        self.rules.setHorizontalHeaderLabels([tr("ui.process"), tr("ui.page")])
        self.rules.horizontalHeader().setStretchLastSection(True)
        self.rules.verticalHeader().hide()
        self.rules.setFixedHeight(130)
        self.rules.setStyleSheet("QTableWidget{background:#0f1117;border:1px solid #262b38;border-radius:8px;}"
                                 "QHeaderView::section{background:#151821;border:none;padding:4px;color:#8b93a7;}")
        lay.addWidget(self.rules)
        rrow = QHBoxLayout()
        add_rule = QPushButton("+ " + tr("ui.add_rule"))
        add_rule.clicked.connect(self._add_rule)
        del_rule = QPushButton("−")
        del_rule.clicked.connect(self._del_rule)
        rrow.addWidget(add_rule, 1)
        rrow.addWidget(del_rule)
        lay.addLayout(rrow)
        self.list.currentRowChanged.connect(self._selected)
        self._loading = False

    # ----------------------------------------------------------------- refresh
    def refresh(self, select_id: str | None = None) -> None:
        self._loading = True
        self.list.clear()
        for p in self.win.profile.pages:
            item = QListWidgetItem(p.name)
            img = icon_image(p.icon or "layers", p.accent, 18)
            if img:
                item.setIcon(QIcon(QPixmap.fromImage(img)))
            item.setData(Qt.ItemDataRole.UserRole, p.id)
            self.list.addItem(item)
        ids = [p.id for p in self.win.profile.pages]
        target = select_id if select_id in ids else (self.win.page.id if self.win.page.id in ids else ids[0] if ids else None)
        if target:
            self.list.setCurrentRow(ids.index(target))
        self._load_rules()
        self._loading = False
        self._fill()

    def _fill(self) -> None:
        p = self.win.page
        self._loading = True
        self.name.setText(p.name)
        self.icon_btn.setText(p.icon or tr("ui.choose_icon"))
        img = icon_image(p.icon or "layers", "#e6e9f0", 16)
        self.icon_btn.setIcon(QIcon(QPixmap.fromImage(img)) if img else QIcon())
        self.accent.set_color(p.accent)
        self.platform.setCurrentIndex(max(0, self.platform.findData(p.platform)))
        self.shift.setChecked("ac_shift_lights" in p.effects)
        self.start.setChecked(self.win.profile.start_page == p.id)
        self._loading = False

    def _load_rules(self) -> None:
        self.rules.blockSignals(True)
        self.rules.setRowCount(0)
        for r in self.win.profile.auto_switch:
            self._rule_row(r.get("process", ""), r.get("page"))
        self.rules.blockSignals(False)

    def _rule_row(self, process: str, page_id: str | None) -> None:
        row = self.rules.rowCount()
        self.rules.insertRow(row)
        item = QTableWidgetItem(process)
        self.rules.setItem(row, 0, item)
        combo = QComboBox()
        for p in self.win.profile.pages:
            combo.addItem(p.name, p.id)
        combo.setCurrentIndex(max(0, combo.findData(page_id)))
        combo.currentIndexChanged.connect(self._save_rules)
        self.rules.setCellWidget(row, 1, combo)
        self.rules.itemChanged.connect(self._save_rules)

    def _save_rules(self, *_):
        rules = []
        for r in range(self.rules.rowCount()):
            proc = (self.rules.item(r, 0).text() if self.rules.item(r, 0) else "").strip()
            combo = self.rules.cellWidget(r, 1)
            if proc and combo:
                rules.append({"process": proc, "page": combo.currentData()})
        self.win.profile.auto_switch = rules
        self.win.changed()

    def _add_rule(self):
        self._rule_row("game.exe", self.win.page.id)
        self._save_rules()

    def _del_rule(self):
        r = self.rules.currentRow()
        if r >= 0:
            self.rules.removeRow(r)
            self._save_rules()

    # ----------------------------------------------------------------- page ops
    def _selected(self, row: int) -> None:
        if self._loading or row < 0:
            return
        pid = self.list.item(row).data(Qt.ItemDataRole.UserRole)
        self.win.open_page(pid)
        self._fill()

    def _reordered(self, *_):
        order = [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        self.win.profile.pages.sort(key=lambda p: order.index(p.id))
        self.win.changed()

    def _add(self):
        p = Page.new(tr("ui.new_page"))
        self.win.profile.pages.append(p)
        self.win.changed()
        self.win.open_page(p.id)
        self.refresh(p.id)

    def _dup(self):
        src = self.win.page
        p = Page.from_dict(copy.deepcopy(src.to_dict()))
        p.id = Page.new("").id
        p.name = f"{src.name} (2)"
        self.win.profile.pages.insert(self.win.profile.pages.index(src) + 1, p)
        self.win.changed()
        self.win.open_page(p.id)
        self.refresh(p.id)

    def _delete(self):
        if len(self.win.profile.pages) <= 1:
            return
        p = self.win.page
        if QMessageBox.question(self, APP_NAME, tr("ui.confirm_delete_page", name=p.name)) != QMessageBox.StandardButton.Yes:
            return
        self.win.profile.pages.remove(p)
        self.win.profile.auto_switch = [r for r in self.win.profile.auto_switch if r.get("page") != p.id]
        self.win.changed()
        self.win.open_page(self.win.profile.pages[0].id)
        self.refresh()

    def _rename(self):
        if not self._loading and self.name.text().strip():
            self.win.page.name = self.name.text().strip()
            self.win.changed()
            self.refresh(self.win.page.id)

    def _pick_icon(self):
        d = IconPicker(self, self.win.page.icon)
        if d.exec():
            self.win.page.icon = d.choice
            self.win.changed()
            self.refresh(self.win.page.id)

    def _accent(self, c):
        self.win.page.accent = c or "#5b8cff"
        self.win.changed()
        self.refresh(self.win.page.id)

    def _platform(self):
        if not self._loading:
            self.win.page.platform = self.platform.currentData()
            self.win.changed()

    def _shift(self, on):
        if self._loading:
            return
        eff = [e for e in self.win.page.effects if e != "ac_shift_lights"]
        if on:
            eff.append("ac_shift_lights")
        self.win.page.effects = eff
        self.win.changed()

    def _start(self, on):
        if not self._loading and on:
            self.win.profile.start_page = self.win.page.id
            self.win.changed()


# --------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self, engine: Engine, profile: Profile, settings: Settings):
        super().__init__()
        self.engine, self.profile, self.settings = engine, profile, settings
        self.page: Page = engine.page
        self.selection = "key0"
        self._clipboard: dict | None = None
        self._quitting = False
        self.setWindowTitle(f"{APP_NAME} {__version__}")
        self.setWindowIcon(app_icon())
        self.resize(1440, 860)

        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._topbar())

        split = QSplitter()
        split.setHandleWidth(1)
        self.pages = PagePanel(self)
        split.addWidget(self.pages)
        center = QWidget()
        cl = QVBoxLayout(center)
        cl.setContentsMargins(24, 12, 24, 12)
        self.view = DeviceView()
        cl.addWidget(self.view, 1)
        self.follow = QCheckBox(tr("ui.follow_device"))
        self.follow.setChecked(True)
        self.follow.setToolTip(tr("ui.follow_device_tip"))
        tips = QHBoxLayout()
        tip = QLabel(tr("ui.view_hint") + "\n" + tr("ui.nav_hint"))
        tip.setObjectName("muted")
        tips.addWidget(tip, 1)
        tips.addWidget(self.follow)
        cl.addLayout(tips)
        split.addWidget(center)
        self.inspector = Inspector(self)
        split.addWidget(self.inspector)
        split.setSizes([310, 720, 410])
        outer.addWidget(split, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(400)
        self._save_timer.timeout.connect(self._save)

        self.view.selected.connect(self.select)
        self.view.swapped.connect(self._swap)
        self.view.context.connect(self._context_menu)
        engine.frame.connect(self._frame)
        engine.page_changed.connect(self._engine_page)
        engine.device_changed.connect(self._device)
        engine.toast.connect(lambda t: self.statusBar().showMessage(t, 4000))

        self._tray()
        self.pages.refresh(self.page.id)
        self.select("key0")
        self._device("")

    # ----------------------------------------------------------------- chrome
    def _topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("panel")
        bar.setFixedHeight(58)
        h = QHBoxLayout(bar)
        h.setContentsMargins(16, 8, 16, 8)
        logo = QLabel()
        logo.setPixmap(QPixmap.fromImage(icon_image("layout-grid", "#5b8cff", 26, 2.4)))
        h.addWidget(logo)
        title = QLabel(APP_NAME)
        title.setObjectName("h1")
        h.addWidget(title)
        self.status = QLabel()
        h.addWidget(self.status)
        h.addStretch()
        sun = QLabel()
        sun.setPixmap(QPixmap.fromImage(icon_image("sun", "#8b93a7", 18)))
        h.addWidget(sun)
        self.bright = QSlider(Qt.Orientation.Horizontal)
        self.bright.setRange(5, 100)
        self.bright.setFixedWidth(140)
        self.bright.setValue(self.profile.brightness)
        self.bright.valueChanged.connect(self._brightness)
        h.addWidget(self.bright)
        h.addSpacing(12)
        for text, fn in ((tr("ui.import_streamdock"), self._import), (tr("ui.export"), self._export),
                         (tr("ui.import_profile"), self._import_profile), (tr("ui.settings"), self._settings)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            h.addWidget(b)
        return bar

    def _tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction(tr("ui.open"), self._show)
        self.tray_pages = menu.addMenu(tr("ui.pages"))
        menu.addSeparator()
        menu.addAction(tr("ui.quit"), self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self._show() if r == QSystemTrayIcon.ActivationReason.Trigger else None)
        self._fill_tray_pages()
        self.tray.show()

    def _fill_tray_pages(self) -> None:
        self.tray_pages.clear()
        for p in self.engine.visible_pages():
            self.tray_pages.addAction(p.name, lambda pid=p.id: self.engine.goto(pid))

    def _show(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, e: QCloseEvent) -> None:
        if self._quitting or not QSystemTrayIcon.isSystemTrayAvailable():
            self._save()
            e.accept()
            return
        e.ignore()
        self.hide()
        self.tray.showMessage(APP_NAME, tr("ui.still_running"), QSystemTrayIcon.MessageIcon.Information, 2500)

    def quit(self) -> None:
        self._quitting = True
        self._save()
        self.engine.stop()
        QApplication.quit()

    # ----------------------------------------------------------------- engine sync
    def _frame(self, key: str, img: QImage) -> None:
        if self.engine.page.id == self.page.id or self.follow.isChecked():
            self.view.set_image(key, img)

    def _engine_page(self, pid: str) -> None:
        if self.follow.isChecked() and pid != self.page.id:
            self.page = self.profile.page(pid) or self.page
            self.pages.refresh(pid)
            self.select(self.selection)

    def _device(self, text: str) -> None:
        if text:
            self.status.setText("●  " + text)
            self.status.setObjectName("pillok")
        else:
            self.status.setText("○  " + tr("ui.no_device"))
            self.status.setObjectName("pill")
        self.status.setStyleSheet("")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    # ----------------------------------------------------------------- editing
    def open_page(self, pid: str) -> None:
        p = self.profile.page(pid)
        if p is None:
            return
        self.page = p
        if self.engine.page.id != pid and p.available_on("windows" if sys.platform == "win32" else "linux"):
            self.engine.goto(pid)
        if self.pages.list.currentItem() is None or self.pages.list.currentItem().data(Qt.ItemDataRole.UserRole) != pid:
            self.pages.refresh(pid)
        else:
            self.pages._fill()
        self.select(self.selection)

    def select(self, target: str) -> None:
        self.selection = target
        self.view.select(target)
        self.inspector.show_target(self.page, target)

    def changed(self) -> None:
        """Called after any edit: the engine reads the profile directly, so just redraw + save soon."""
        self.engine._visuals.clear()
        self._save_timer.start()

    def _save(self) -> None:
        try:
            self.profile.save()
        except OSError as exc:
            self.statusBar().showMessage(str(exc), 6000)
        self.engine.reload()
        self._fill_tray_pages()

    def _brightness(self, v: int) -> None:
        self.profile.brightness = v
        if self.engine.device:
            self.engine.device.set_brightness(v)
        self._save_timer.start()

    def _slot_of(self, key: str) -> Slot | None:
        if key.startswith("key"):
            return self.page.keys[int(key[3:])]
        if key.startswith("zone"):
            return self.page.zones[int(key[4:])]
        return None

    def _swap(self, a: str, b: str) -> None:
        lst = self.page.keys if a.startswith("key") else self.page.zones
        ia, ib = int(a[-1]), int(b[-1])
        lst[ia], lst[ib] = lst[ib], lst[ia]
        self.changed()
        self.select(b)

    def _context_menu(self, key: str, pos) -> None:
        slot = self._slot_of(key)
        if slot is None:
            return
        m = QMenu(self)
        m.addAction(tr("ui.copy"), lambda: setattr(self, "_clipboard", copy.deepcopy(slot.to_dict() or {})))
        paste = m.addAction(tr("ui.paste"), lambda: self._paste(key))
        paste.setEnabled(self._clipboard is not None)
        m.addSeparator()
        m.addAction(tr("ui.clear_slot"), lambda: self._paste(key, {}))
        m.exec(pos)

    def _paste(self, key: str, data: dict | None = None) -> None:
        new = Slot.from_dict(copy.deepcopy(self._clipboard if data is None else data))
        lst = self.page.keys if key.startswith("key") else self.page.zones
        lst[int(key[-1])] = new
        self.changed()
        self.select(key)

    # ----------------------------------------------------------------- import / export
    def ac_bindings(self) -> dict:
        return self.engine.ac_bindings()

    def _import(self) -> None:
        from ..importers.streamdock import default_dir, import_profiles
        base = default_dir()
        if not base.is_dir():
            d = QFileDialog.getExistingDirectory(self, tr("ui.import_streamdock"))
            if not d:
                return
            base = Path(d)
        pages = import_profiles(base)
        if not pages:
            QMessageBox.information(self, APP_NAME, tr("ui.nothing_imported"))
            return
        self.profile.pages.extend(pages)
        self.changed()
        self.pages.refresh(pages[0].id)
        self.open_page(pages[0].id)
        QMessageBox.information(self, APP_NAME, tr("ui.imported_n", n=len(pages)))

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, tr("ui.export"), "freedock-profile.json", "JSON (*.json)")
        if path:
            Path(path).write_text(json.dumps(self.profile.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _import_profile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("ui.import_profile"), "", "JSON (*.json)")
        if not path:
            return
        try:
            other = Profile.load(Path(path))
        except (OSError, ValueError, KeyError) as exc:
            QMessageBox.warning(self, APP_NAME, str(exc))
            return
        known = {p.id for p in self.profile.pages}
        for p in other.pages:
            if p.id in known:
                p.id = Page.new("").id
            self.profile.pages.append(p)
        self.changed()
        self.pages.refresh()

    def _settings(self) -> None:
        d = SettingsDialog(self)
        if d.exec():
            lang_changed = d.apply()
            self.engine.lang = self.settings.language
            self.changed()
            if lang_changed:
                QMessageBox.information(self, APP_NAME, tr("ui.restart_for_language"))


def run_gui(app: QApplication, profile: Profile, settings: Settings, minimized: bool = False) -> int:
    load_fonts()
    app.setStyleSheet(STYLE)
    app.setQuitOnLastWindowClosed(False)
    set_language(settings.language)
    engine = Engine(profile, settings)
    win = MainWindow(engine, profile, settings)
    from ..cli import serve_instance
    win._instance_server = serve_instance({"show": win._show, "quit": win.quit, "ping": lambda: None})
    engine.start()
    import signal
    signal.signal(signal.SIGINT, lambda *a: win.quit())
    app.aboutToQuit.connect(lambda: engine.stop())
    if not minimized:
        win.show()
    code = app.exec()
    engine.stop()
    return code
