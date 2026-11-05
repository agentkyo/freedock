"""Reusable editor widgets: colour button, hotkey recorder, icon picker, action & widget forms."""
from __future__ import annotations

import json
from functools import lru_cache

from PySide6.QtCore import QAbstractListModel, QModelIndex, QSize, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeyEvent, QPixmap
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListView, QListWidget,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .. import actions as act
from ..actions import Field
from ..i18n import tr
from ..render.painter import ICON_DIR, available_icons, icon_image
from ..widgets import WIDGETS


# --------------------------------------------------------------------------- small widgets
class ColorButton(QPushButton):
    changed = Signal(str)

    def __init__(self, color: str | None = None, allow_none: bool = False):
        super().__init__()
        self._allow_none = allow_none
        self.setFixedSize(38, 28)
        self.set_color(color)
        self.clicked.connect(self._pick)

    def set_color(self, color: str | None) -> None:
        self.color = color
        bg = color or "transparent"
        border = "#3a4256" if color else "#5b6478"
        self.setStyleSheet(f"background:{bg}; border:2px {'solid' if color else 'dashed'} {border}; border-radius:8px;")
        self.setToolTip(color or tr("ui.none"))

    def _pick(self) -> None:
        c = QColorDialog.getColor(QColor(self.color or "#5b8cff"), self, tr("ui.pick_color"))
        if c.isValid():
            self.set_color(c.name())
            self.changed.emit(c.name())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.RightButton and self._allow_none:
            self.set_color(None)
            self.changed.emit("")
            return
        super().mouseReleaseEvent(e)


QT_KEYS = {
    Qt.Key.Key_Escape: "esc", Qt.Key.Key_Tab: "tab", Qt.Key.Key_Backspace: "backspace", Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter", Qt.Key.Key_Insert: "insert", Qt.Key.Key_Delete: "delete", Qt.Key.Key_Pause: "pause",
    Qt.Key.Key_Print: "printscreen", Qt.Key.Key_Home: "home", Qt.Key.Key_End: "end", Qt.Key.Key_Left: "left",
    Qt.Key.Key_Up: "up", Qt.Key.Key_Right: "right", Qt.Key.Key_Down: "down", Qt.Key.Key_PageUp: "pageup",
    Qt.Key.Key_PageDown: "pagedown", Qt.Key.Key_Space: "space", Qt.Key.Key_Minus: "minus", Qt.Key.Key_Equal: "equal",
    Qt.Key.Key_Plus: "plus", Qt.Key.Key_Comma: "comma", Qt.Key.Key_Period: "period", Qt.Key.Key_Slash: "slash",
    Qt.Key.Key_Backslash: "backslash", Qt.Key.Key_Semicolon: "semicolon", Qt.Key.Key_Apostrophe: "quote",
    Qt.Key.Key_BracketLeft: "bracketleft", Qt.Key.Key_BracketRight: "bracketright", Qt.Key.Key_QuoteLeft: "grave",
    Qt.Key.Key_MediaPlay: "playpause", Qt.Key.Key_MediaNext: "nexttrack", Qt.Key.Key_MediaPrevious: "prevtrack",
    Qt.Key.Key_VolumeUp: "volumeup", Qt.Key.Key_VolumeDown: "volumedown", Qt.Key.Key_VolumeMute: "mute",
    Qt.Key.Key_CapsLock: "capslock", Qt.Key.Key_NumLock: "numlock", Qt.Key.Key_ScrollLock: "scrolllock",
}


class KeyCaptureEdit(QWidget):
    """Text field + "record" button: press the combination you want and it is written for you."""
    changed = Signal(str)

    def __init__(self, value: str = ""):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(value)
        self.edit.setPlaceholderText("ctrl+shift+esc")
        self.btn = QPushButton("●  " + tr("ui.record"))
        self.btn.setCheckable(True)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.btn)
        self.edit.editingFinished.connect(lambda: self.changed.emit(self.edit.text().strip()))
        self.btn.toggled.connect(self._toggle)

    def _toggle(self, on: bool) -> None:
        self.btn.setText(("■  " + tr("ui.recording")) if on else ("●  " + tr("ui.record")))
        if on:
            self.edit.setText("")
            self.grabKeyboard()
        else:
            self.releaseKeyboard()

    def keyPressEvent(self, e: QKeyEvent):
        if not self.btn.isChecked():
            return super().keyPressEvent(e)
        key = e.key()
        if key in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            return
        mods = e.modifiers()
        parts = []
        if mods & Qt.KeyboardModifier.ControlModifier:
            parts.append("ctrl")
        if mods & Qt.KeyboardModifier.ShiftModifier:
            parts.append("shift")
        if mods & Qt.KeyboardModifier.AltModifier:
            parts.append("alt")
        if mods & Qt.KeyboardModifier.MetaModifier:
            parts.append("win")
        if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
            name = f"f{key - Qt.Key.Key_F1 + 1}"
        elif Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
            name = chr(key).lower()
        else:
            name = QT_KEYS.get(Qt.Key(key), f"vk:{e.nativeVirtualKey():#04x}" if e.nativeVirtualKey() else "")
        if mods & Qt.KeyboardModifier.KeypadModifier and name.isdigit():
            name = "num" + name
        if name:
            parts.append(name)
            self.edit.setText("+".join(parts))
            self.btn.setChecked(False)
            self.changed.emit(self.edit.text())


# --------------------------------------------------------------------------- icons
@lru_cache(maxsize=1)
def _icon_tags() -> dict[str, str]:
    try:
        tags = json.loads((ICON_DIR / "tags.json").read_text(encoding="utf-8"))
        return {k: " ".join(v) for k, v in tags.items()}
    except (OSError, ValueError):
        return {}


class _IconModel(QAbstractListModel):
    def __init__(self):
        super().__init__()
        self.names = available_icons()
        self.tags = _icon_tags()
        self._cache: dict[str, QIcon] = {}

    def rowCount(self, parent=QModelIndex()):
        return len(self.names)

    def data(self, index, role):
        name = self.names[index.row()]
        if role == Qt.ItemDataRole.DecorationRole:
            if name not in self._cache:
                img = icon_image(name, "#e6e9f0", 36)
                self._cache[name] = QIcon(QPixmap.fromImage(img)) if img else QIcon()
            return self._cache[name]
        if role == Qt.ItemDataRole.ToolTipRole:
            return name
        if role == Qt.ItemDataRole.UserRole:
            return f"{name} {self.tags.get(name, '')}"
        return None


class IconPicker(QDialog):
    def __init__(self, parent=None, current: str | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("ui.choose_icon"))
        self.resize(640, 560)
        self.choice: str | None = current
        lay = QVBoxLayout(self)
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("ui.search_icons"))
        lay.addWidget(self.search)
        self.model = _IconModel()
        self.proxy = QSortFilterProxyModel()
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterRole(Qt.ItemDataRole.UserRole)
        self.proxy.setFilterCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.view = QListView()
        self.view.setViewMode(QListView.ViewMode.IconMode)
        self.view.setIconSize(QSize(36, 36))
        self.view.setGridSize(QSize(64, 64))
        self.view.setUniformItemSizes(True)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setModel(self.proxy)
        lay.addWidget(self.view, 1)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        clear = bb.addButton(tr("ui.no_icon"), QDialogButtonBox.ButtonRole.ResetRole)
        lay.addWidget(bb)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.view.doubleClicked.connect(self._accept_index)
        bb.accepted.connect(lambda: self._accept_index(self.view.currentIndex()))
        bb.rejected.connect(self.reject)
        clear.clicked.connect(self._clear)
        self.search.setFocus()

    def _accept_index(self, idx) -> None:
        if idx.isValid():
            self.choice = self.model.names[self.proxy.mapToSource(idx).row()]
            self.accept()

    def _clear(self) -> None:
        self.choice = None
        self.accept()


# --------------------------------------------------------------------------- forms
class FieldForm(QWidget):
    """Builds inputs for a list of :class:`Field` and reports the values as a dict."""
    changed = Signal()

    def __init__(self, fields: tuple[Field, ...], values: dict, context):
        super().__init__()
        self.fields = fields
        self.context = context          # main window: pages(), ac_bindings()
        self.inputs: dict[str, object] = {}
        self.values = dict(values)
        form = QFormLayout(self)
        form.setContentsMargins(0, 4, 0, 0)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        for f in fields:
            w = self._make(f, values.get(f.name, f.default))
            if w is not None:
                form.addRow(tr(f"field.{f.name}"), w)

    def _set(self, name, value) -> None:
        self.values[name] = value
        self.changed.emit()

    def _make(self, f: Field, value):
        n = f.name
        if f.kind == "keys":
            w = KeyCaptureEdit(value or "")
            w.changed.connect(lambda v, n=n: self._set(n, v))
        elif f.kind == "text":
            w = QLineEdit(str(value or ""))
            w.editingFinished.connect(lambda w=w, n=n: self._set(n, w.text()))
        elif f.kind == "multiline":
            w = QPlainTextEdit(str(value or ""))
            w.setFixedHeight(70)
            w.textChanged.connect(lambda w=w, n=n: self._set(n, w.toPlainText()))
        elif f.kind in ("int", "float"):
            w = QSpinBox()
            w.setRange(-100000, 100000)
            w.setValue(int(value or 0))
            w.valueChanged.connect(lambda v, n=n: self._set(n, v))
        elif f.kind == "bool":
            w = QCheckBox()
            w.setChecked(bool(value))
            w.toggled.connect(lambda v, n=n: self._set(n, v))
        elif f.kind == "choice":
            w = QComboBox()
            for val, label in f.options:
                w.addItem(tr(label), val)
            i = w.findData(value)
            w.setCurrentIndex(max(0, i))
            w.currentIndexChanged.connect(lambda _, w=w, n=n: self._set(n, w.currentData()))
        elif f.kind == "page":
            w = QComboBox()
            for val, label in (("next", tr("page.next")), ("prev", tr("page.prev")), ("back", tr("page.back"))):
                w.addItem(label, val)
            for p in self.context.profile.pages:
                w.addItem(p.name, p.id)
            w.setCurrentIndex(max(0, w.findData(value)))
            w.currentIndexChanged.connect(lambda _, w=w, n=n: self._set(n, w.currentData()))
        elif f.kind == "file":
            w = QWidget()
            h = QHBoxLayout(w)
            h.setContentsMargins(0, 0, 0, 0)
            e = QLineEdit(str(value or ""))
            b = QPushButton("…")
            b.setFixedWidth(34)
            h.addWidget(e, 1)
            h.addWidget(b)
            e.editingFinished.connect(lambda e=e, n=n: self._set(n, e.text()))

            def browse(_=False, e=e, n=n):
                path, _f = QFileDialog.getOpenFileName(self, tr("ui.choose_file"))
                if path:
                    e.setText(path)
                    self._set(n, path)
            b.clicked.connect(browse)
        elif f.kind == "ac_binding":
            from ..integrations.assetto import FRIENDLY
            w = QComboBox()
            w.setEditable(True)
            bindings = self.context.ac_bindings()
            names = list(dict.fromkeys(list(FRIENDLY) + sorted(bindings)))
            for sec in names:
                b = bindings.get(sec)
                label = f"{FRIENDLY.get(sec, sec)}  —  {b.text if b else tr('ac.no_key')}"
                w.addItem(label, sec)
            i = w.findData(value)
            if i < 0 and value:
                w.addItem(str(value), value)
                i = w.count() - 1
            w.setCurrentIndex(max(0, i))
            w.currentIndexChanged.connect(lambda _, w=w, n=n: self._set(n, w.currentData()))
        elif f.kind == "actions":
            w = MultiActionEditor(list(value or []), self.context)
            w.changed.connect(lambda v, n=n: self._set(n, v))
        else:
            return None
        self.inputs[n] = w
        return w


class ActionEditor(QWidget):
    """Action type selector + generated form. Emits the full action dict (or None)."""
    changed = Signal(object)

    def __init__(self, action: dict | None, context, allow_none: bool = True):
        super().__init__()
        self.context = context
        self.action = dict(action) if action else None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        if allow_none:
            self.combo.addItem(tr("action.none"), None)
        for cat in ("system", "media", "deck", "web", "games"):
            for t in act.REGISTRY.values():
                if t.category == cat:
                    self.combo.addItem(QIcon(QPixmap.fromImage(icon_image(t.icon, "#e6e9f0", 18))),
                                       f"{tr('cat.' + cat)} · {tr('action.' + t.id)}", t.id)
        lay.addWidget(self.combo)
        self.form_holder = QVBoxLayout()
        lay.addLayout(self.form_holder)
        self.form: FieldForm | None = None
        self.combo.setCurrentIndex(max(0, self.combo.findData(self.action.get("type") if self.action else None)))
        self._build()
        self.combo.currentIndexChanged.connect(self._type_changed)

    def _type_changed(self) -> None:
        t = self.combo.currentData()
        if t is None:
            self.action = None
        else:
            spec = act.REGISTRY[t]
            self.action = {"type": t, **{f.name: f.default for f in spec.fields}}
        self._build()
        self.changed.emit(self.action)

    def _build(self) -> None:
        if self.form is not None:
            self.form.setParent(None)
            self.form.deleteLater()
            self.form = None
        if not self.action:
            return
        spec = act.REGISTRY.get(self.action.get("type"))
        if spec is None or not spec.fields:
            return
        self.form = FieldForm(spec.fields, self.action, self.context)
        self.form.changed.connect(self._form_changed)
        self.form_holder.addWidget(self.form)

    def _form_changed(self) -> None:
        self.action = {"type": self.action["type"], **self.form.values}
        self.changed.emit(self.action)


class MultiActionEditor(QWidget):
    changed = Signal(list)

    def __init__(self, steps: list, context):
        super().__init__()
        self.steps, self.context = steps, context
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setFixedHeight(110)
        lay.addWidget(self.list)
        row = QHBoxLayout()
        for text, fn in (("+", self._add), ("✎", self._edit), ("−", self._remove), ("↑", lambda: self._move(-1)),
                         ("↓", lambda: self._move(1))):
            b = QPushButton(text)
            b.setFixedWidth(36)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        lay.addLayout(row)
        self._refresh()

    def _refresh(self) -> None:
        self.list.clear()
        for s in self.steps:
            self.list.addItem(act.describe(s))

    def _dialog(self, a):
        d = QDialog(self)
        d.setWindowTitle(tr("action.multi"))
        d.resize(420, 260)
        lay = QVBoxLayout(d)
        ed = ActionEditor(a, self.context, allow_none=False)
        if a is None:
            ed._type_changed()
        lay.addWidget(ed)
        lay.addStretch()
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        lay.addWidget(bb)
        return ed.action if d.exec() else None

    def _add(self):
        a = self._dialog(None)
        if a:
            self.steps.append(a)
            self._emit()

    def _edit(self):
        i = self.list.currentRow()
        if i >= 0 and (a := self._dialog(self.steps[i])):
            self.steps[i] = a
            self._emit()

    def _remove(self):
        i = self.list.currentRow()
        if i >= 0:
            del self.steps[i]
            self._emit()

    def _move(self, d):
        i = self.list.currentRow()
        j = i + d
        if 0 <= i < len(self.steps) and 0 <= j < len(self.steps):
            self.steps[i], self.steps[j] = self.steps[j], self.steps[i]
            self._emit()
            self.list.setCurrentRow(j)

    def _emit(self):
        self._refresh()
        self.changed.emit(list(self.steps))


class WidgetEditor(QWidget):
    changed = Signal(object)

    def __init__(self, widget: dict | None, context):
        super().__init__()
        self.context = context
        self.widget = dict(widget) if widget else None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.combo = QComboBox()
        self.combo.addItem(tr("widget.none"), None)
        for cat in ("time", "system", "media", "deck", "web", "games"):
            for w in WIDGETS.values():
                if w.category == cat:
                    self.combo.addItem(QIcon(QPixmap.fromImage(icon_image(w.icon, "#e6e9f0", 18))),
                                       f"{tr('cat.' + cat)} · {tr('widget.' + w.id)}", w.id)
        lay.addWidget(self.combo)
        self.holder = QVBoxLayout()
        lay.addLayout(self.holder)
        self.form: FieldForm | None = None
        self.combo.setCurrentIndex(max(0, self.combo.findData(self.widget.get("type") if self.widget else None)))
        self._build()
        self.combo.currentIndexChanged.connect(self._type_changed)

    def _type_changed(self):
        t = self.combo.currentData()
        if t is None:
            self.widget = None
        else:
            self.widget = {"type": t, **{f.name: f.default for f in WIDGETS[t].fields}}
        self._build()
        self.changed.emit(self.widget)

    def _build(self):
        if self.form is not None:
            self.form.setParent(None)
            self.form.deleteLater()
            self.form = None
        if not self.widget:
            return
        spec = WIDGETS.get(self.widget.get("type"))
        if spec and spec.fields:
            self.form = FieldForm(spec.fields, self.widget, self.context)
            self.form.changed.connect(lambda: self._emit())
            self.holder.addWidget(self.form)

    def _emit(self):
        self.widget = {"type": self.widget["type"], **self.form.values}
        self.changed.emit(self.widget)


def section(title: str) -> QLabel:
    lbl = QLabel(title)
    lbl.setObjectName("h2")
    return lbl
