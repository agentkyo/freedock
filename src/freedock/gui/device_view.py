"""A live, clickable replica of the N4. Shows exactly what the device shows, animations included."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (QColor, QImage, QLinearGradient, QMouseEvent, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import QSizePolicy, QWidget

from ..device import N4

KEY = 112
GAP = 18
STRIP_W = 5 * KEY + 4 * GAP
ZGAP = 8
# the strip is drawn to the keys' width; zones keep the panel's aspect ratio
ZONE_W = (STRIP_W - 3 * ZGAP) / 4
ZONE_H = ZONE_W * N4.zone_size[1] / N4.zone_size[0]


class DeviceView(QWidget):
    selected = Signal(str)            # "key3", "zone1", "knob2", "swipe"
    swapped = Signal(str, str)        # drag from one slot onto another
    context = Signal(str, object)     # right-click (slot, global pos)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.images: dict[str, QImage] = {}
        self.current: str | None = "key0"
        self._hover: str | None = None
        self._drag_from: str | None = None
        self._press_pos = QPointF()
        self.setMouseTracking(True)
        self.setMinimumSize(560, 470)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    # ------------------------------------------------------------------ geometry (device units)
    W = 5 * KEY + 4 * GAP + 2 * 40
    H = 40 + 2 * KEY + GAP + 26 + ZONE_H + 26 + 70 + 40

    def _rects(self) -> dict[str, QRectF]:
        r: dict[str, QRectF] = {}
        x0, y0 = 40, 40
        for i in range(10):
            row, col = divmod(i, 5)
            r[f"key{i}"] = QRectF(x0 + col * (KEY + GAP), y0 + row * (KEY + GAP), KEY, KEY)
        strip_w = STRIP_W
        zgap = ZGAP
        zy = y0 + 2 * KEY + GAP + 26
        for i in range(4):
            r[f"zone{i}"] = QRectF(x0 + i * (ZONE_W + zgap), zy, ZONE_W, ZONE_H)
            cx = x0 + i * (ZONE_W + zgap) + ZONE_W / 2
            r[f"knob{i}"] = QRectF(cx - 34, zy + ZONE_H + 26, 68, 68)
        r["swipe"] = QRectF(x0, zy - 20, strip_w, 14)
        return r

    def _transform(self) -> tuple[float, float, float]:
        s = min(self.width() / self.W, self.height() / self.H)
        return s, (self.width() - self.W * s) / 2, (self.height() - self.H * s) / 2

    def _hit(self, pos: QPointF) -> str | None:
        s, ox, oy = self._transform()
        p = QPointF((pos.x() - ox) / s, (pos.y() - oy) / s)
        for name, rect in self._rects().items():
            if rect.adjusted(-4, -4, 4, 4).contains(p):
                return name
        return None

    # ------------------------------------------------------------------ data
    def set_image(self, key: str, img: QImage) -> None:
        self.images[key] = img
        self.update()

    def select(self, key: str) -> None:
        self.current = key
        self.update()

    # ------------------------------------------------------------------ painting
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        s, ox, oy = self._transform()
        p.translate(ox, oy)
        p.scale(s, s)

        body = QRectF(6, 6, self.W - 12, self.H - 12)
        g = QLinearGradient(0, 0, 0, self.H)
        g.setColorAt(0, QColor("#2a2e38"))
        g.setColorAt(1, QColor("#16181e"))
        path = QPainterPath()
        path.addRoundedRect(body, 34, 34)
        p.fillPath(path, g)
        p.setPen(QPen(QColor(255, 255, 255, 22), 1.5))
        p.drawPath(path)

        rects = self._rects()
        # glass panel behind the keys and strip
        panel = QRectF(rects["key0"].left() - 14, rects["key0"].top() - 14,
                       5 * KEY + 4 * GAP + 28, rects["zone0"].bottom() - rects["key0"].top() + 28)
        pp = QPainterPath()
        pp.addRoundedRect(panel, 18, 18)
        p.fillPath(pp, QColor("#050608"))

        for name, rect in rects.items():
            if name.startswith(("key", "zone")):
                img = self.images.get(name)
                if img is not None:
                    p.drawImage(rect, img)
                else:
                    p.fillRect(rect, QColor("#0a0c11"))
            elif name.startswith("knob"):
                self._knob(p, rect)
            elif name == "swipe":
                c = QColor("#5b8cff" if self.current == "swipe" else "#3a4256")
                c.setAlpha(200 if self._hover == "swipe" or self.current == "swipe" else 110)
                p.setPen(QPen(c, 2, Qt.PenStyle.DashLine))
                y = rect.center().y()
                p.drawLine(QPointF(rect.left() + 40, y), QPointF(rect.right() - 40, y))
                p.drawText(rect.adjusted(0, -16, 0, 0), Qt.AlignmentFlag.AlignCenter, "‹  swipe  ›")

        for name in (self._hover, self.current):
            if name and name in rects and name != "swipe":
                rect = rects[name].adjusted(-5, -5, 5, 5)
                pen = QPen(QColor("#5b8cff") if name == self.current else QColor(255, 255, 255, 70),
                           3 if name == self.current else 1.5)
                p.setPen(pen)
                p.setBrush(Qt.BrushStyle.NoBrush)
                if name.startswith("knob"):
                    p.drawEllipse(rect)
                else:
                    p.drawRoundedRect(rect, 16, 16)
        p.end()

    def _knob(self, p: QPainter, rect: QRectF) -> None:
        g = QRadialGradient(rect.center() - QPointF(10, 12), rect.width() * 0.8)
        g.setColorAt(0, QColor("#4a505e"))
        g.setColorAt(1, QColor("#14161b"))
        p.setPen(QPen(QColor(0, 0, 0, 160), 2))
        p.setBrush(g)
        p.drawEllipse(rect)
        inner = rect.adjusted(10, 10, -10, -10)
        p.setPen(QPen(QColor(255, 255, 255, 25), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        for k in range(0, 360, 15):
            p.save()
            p.translate(rect.center())
            p.rotate(k)
            p.drawLine(QPointF(0, -inner.height() / 2), QPointF(0, -inner.height() / 2 + 5))
            p.restore()
        mark = QPen(QColor("#5b8cff"), 3)
        mark.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(mark)
        p.drawLine(QPointF(rect.center().x(), rect.top() + 8), QPointF(rect.center().x(), rect.top() + 18))

    # ------------------------------------------------------------------ mouse
    def mouseMoveEvent(self, e: QMouseEvent):
        hit = self._hit(e.position())
        if hit != self._hover:
            self._hover = hit
            self.update()
        self.setCursor(Qt.CursorShape.PointingHandCursor if hit else Qt.CursorShape.ArrowCursor)

    def leaveEvent(self, _):
        self._hover = None
        self.update()

    def mousePressEvent(self, e: QMouseEvent):
        hit = self._hit(e.position())
        if not hit:
            return
        if e.button() == Qt.MouseButton.RightButton:
            self.selected.emit(hit)
            self.context.emit(hit, e.globalPosition().toPoint())
            return
        self._drag_from = hit if hit.startswith(("key", "zone")) else None
        self._press_pos = e.position()
        self.selected.emit(hit)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if self._drag_from and (e.position() - self._press_pos).manhattanLength() > 12:
            target = self._hit(e.position())
            if target and target != self._drag_from and target[:3] == self._drag_from[:3]:
                self.swapped.emit(self._drag_from, target)
        self._drag_from = None
