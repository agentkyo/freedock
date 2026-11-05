"""Right-hand panel: edit whatever is selected on the device view (key, strip zone, knob, swipe)."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (QComboBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from ..i18n import tr
from ..profile import Knob, Page, Slot
from ..render.painter import icon_image
from .editors import ActionEditor, ColorButton, IconPicker, WidgetEditor, section
from .theme import PALETTES


class Inspector(QScrollArea):
    changed = Signal()

    def __init__(self, context):
        super().__init__()
        self.context = context
        self.setObjectName("panel")
        self.setWidgetResizable(True)
        self.setMinimumWidth(380)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.target: str | None = None

    def show_target(self, page: Page, target: str) -> None:
        self.page, self.target = page, target
        body = QWidget()
        body.setObjectName("panel")
        lay = QVBoxLayout(body)
        lay.setContentsMargins(16, 12, 16, 16)
        lay.setSpacing(6)
        if target.startswith("key"):
            self._slot_ui(lay, page.keys[int(target[3:])], tr("ui.key_n", n=int(target[3:]) + 1), long_press=True)
        elif target.startswith("zone"):
            i = int(target[4:])
            self._slot_ui(lay, page.zones[i], tr("ui.zone_n", n=i + 1), long_press=False)
            hint = QLabel(tr("ui.zone_hint"))
            hint.setWordWrap(True)
            hint.setObjectName("muted")
            lay.addWidget(hint)
        elif target.startswith("knob"):
            self._knob_ui(lay, page.knobs[int(target[4:])], int(target[4:]))
        elif target == "swipe":
            self._swipe_ui(lay, page)
        lay.addStretch()
        for combo in body.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(12)
        self.setWidget(body)

    # ------------------------------------------------------------------ slots
    def _slot_ui(self, lay: QVBoxLayout, slot: Slot, title: str, long_press: bool) -> None:
        h = QLabel(title)
        h.setObjectName("h1")
        lay.addWidget(h)

        lay.addWidget(section(tr("ui.appearance")))
        form = QFormLayout()
        label = QLineEdit(slot.label)
        label.setPlaceholderText(tr("ui.label_hint"))
        label.textChanged.connect(lambda t: self._edit(slot, "label", t))
        form.addRow(tr("ui.label"), label)

        icon_row = QHBoxLayout()
        icon_btn = QPushButton(slot.icon or tr("ui.choose_icon"))
        if slot.icon and (img := icon_image(slot.icon, "#e6e9f0", 18)):
            icon_btn.setIcon(QIcon(QPixmap.fromImage(img)))

        def pick_icon():
            d = IconPicker(self, slot.icon)
            if d.exec():
                self._edit(slot, "icon", d.choice)
                self.show_target(self.page, self.target)
        icon_btn.clicked.connect(pick_icon)
        img_btn = QPushButton(tr("ui.picture"))
        img_btn.setToolTip(slot.image or "")

        def pick_image():
            path, _ = QFileDialog.getOpenFileName(self, tr("ui.picture"), "", "Images (*.png *.jpg *.jpeg *.bmp *.gif)")
            self._edit(slot, "image", path or None)
        img_btn.clicked.connect(pick_image)
        icon_row.addWidget(icon_btn, 1)
        icon_row.addWidget(img_btn)
        form.addRow(tr("ui.icon"), icon_row)

        colors = QHBoxLayout()
        for key, tip in (("bg", "ui.color_bg"), ("bg2", "ui.color_bg2"), ("accent", "ui.color_accent"), ("fg", "ui.color_fg")):
            b = ColorButton(slot.style.get(key), allow_none=True)
            b.setToolTip(tr(tip))
            b.changed.connect(lambda c, k=key: self._style(slot, k, c))
            colors.addWidget(b)
        colors.addStretch()
        form.addRow(tr("ui.colors"), colors)
        lay.addLayout(form)

        pal = QGridLayout()
        for i, (name, bg, bg2, accent) in enumerate(PALETTES):
            b = QPushButton()
            b.setFixedHeight(26)
            b.setToolTip(name)
            b.setStyleSheet(f"background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 {bg}, stop:1 {bg2});"
                            f"border: 2px solid {accent}; border-radius: 8px;")
            b.clicked.connect(lambda _=False, bg=bg, bg2=bg2, a=accent: self._palette(slot, bg, bg2, a))
            pal.addWidget(b, i // 5, i % 5)
        lay.addLayout(pal)

        lay.addWidget(section(tr("ui.live_widget")))
        we = WidgetEditor(slot.widget, self.context)
        we.changed.connect(lambda w: self._edit(slot, "widget", w))
        lay.addWidget(we)

        lay.addWidget(section(tr("ui.on_press")))
        ae = ActionEditor(slot.action, self.context)
        ae.changed.connect(lambda a: self._edit(slot, "action", a))
        lay.addWidget(ae)
        if long_press:
            lay.addWidget(section(tr("ui.on_long_press")))
            le = ActionEditor(slot.long_action, self.context)
            le.changed.connect(lambda a: self._edit(slot, "long_action", a))
            lay.addWidget(le)

        clear = QPushButton(tr("ui.clear_slot"))
        clear.setObjectName("danger")
        clear.clicked.connect(lambda: self._clear(slot))
        lay.addSpacing(10)
        lay.addWidget(clear)

    def _knob_ui(self, lay, knob: Knob, i: int) -> None:
        h = QLabel(tr("ui.knob_n", n=i + 1))
        h.setObjectName("h1")
        lay.addWidget(h)
        hint = QLabel(tr("ui.knob_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        for attr, title in (("left", "ui.rotate_left"), ("right", "ui.rotate_right"), ("press", "ui.knob_press")):
            lay.addWidget(section(tr(title)))
            ed = ActionEditor(getattr(knob, attr), self.context)
            ed.changed.connect(lambda a, attr=attr: self._edit(knob, attr, a))
            lay.addWidget(ed)

    def _swipe_ui(self, lay, page: Page) -> None:
        h = QLabel(tr("ui.swipe"))
        h.setObjectName("h1")
        lay.addWidget(h)
        hint = QLabel(tr("ui.swipe_hint"))
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        lay.addWidget(hint)
        for attr, title in (("swipe_left", "ui.swipe_left"), ("swipe_right", "ui.swipe_right")):
            lay.addWidget(section(tr(title)))
            ed = ActionEditor(getattr(page, attr), self.context)
            ed.changed.connect(lambda a, attr=attr: self._edit(page, attr, a))
            lay.addWidget(ed)

    # ------------------------------------------------------------------ mutations
    def _edit(self, obj, attr, value) -> None:
        setattr(obj, attr, value or None if attr != "label" else value)
        self.changed.emit()

    def _style(self, slot: Slot, key: str, color: str) -> None:
        if color:
            slot.style[key] = color
        else:
            slot.style.pop(key, None)
        self.changed.emit()

    def _palette(self, slot: Slot, bg: str, bg2: str, accent: str) -> None:
        slot.style.update(bg=bg, bg2=bg2, accent=accent)
        self.changed.emit()
        self.show_target(self.page, self.target)

    def _clear(self, slot: Slot) -> None:
        slot.label, slot.icon, slot.image = "", None, None
        slot.style, slot.action, slot.long_action, slot.widget = {}, None, None, None
        self.changed.emit()
        self.show_target(self.page, self.target)
