import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication.instance() or QGuiApplication([])

from freedock.render import Fx, Visual, render, to_device_jpeg  # noqa: E402
from freedock.widgets import WIDGETS  # noqa: E402


def test_render_sizes_and_jpeg_limit():
    for w, h in ((112, 112), (176, 112)):
        img = render(Visual(icon="volume-2", label="Volume", value="64%", progress=0.64, active=True), w, h,
                     Fx(press=0.5, ripple=0.3, flash=0.2))
        assert (img.width(), img.height()) == (w, h)
        data = to_device_jpeg(img)
        assert data[:2] == b"\xff\xd8" and len(data) <= 10240


def test_missing_icon_is_harmless():
    render(Visual(icon="this-icon-does-not-exist", label="x"), 112, 112)


def test_every_widget_field_has_a_default():
    for w in WIDGETS.values():
        for f in w.fields:
            assert f.default is not None or f.kind in ("text",), (w.id, f.name)


def test_boot_animation_squares_timeline():
    from freedock.render import boot
    assert all(boot.key_state(i, 0.0)[1] == 0 for i in range(10))           # dark at start
    assert all(boot.key_state(i, 1.3)[1] == 1 for i in range(10))           # every square lit
    assert all(boot.key_state(i, boot.DURATION)[1] == 0 for i in range(10))  # gone before the page shows
    first, last = boot.RING[0], boot.RING[-1]
    assert boot.key_state(first, 0.2)[1] > 0 and boot.key_state(last, 0.2)[1] == 0   # chaser order
    img = boot.key_tile(first, 1.0, (112, 112))
    assert img.pixelColor(56, 56).blue() > 100 and img.pixelColor(2, 2).value() < 10
