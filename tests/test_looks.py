"""Customisation, animations, notifications and profile upgrades."""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QGuiApplication  # noqa: E402

app = QGuiApplication.instance() or QGuiApplication([])

from freedock import style as looks  # noqa: E402
from freedock.notify import NotifyCenter  # noqa: E402
from freedock.presets import add_new_presets, build_default_profile  # noqa: E402
from freedock.profile import Profile  # noqa: E402
from freedock.render.painter import ANIMATIONS, Visual, picture, render  # noqa: E402


def test_style_cascade_theme_page_slot():
    st = looks.merged({"font": "Inter", "bg": "#111111"}, {"bg": "#222222", "anim": "glow"}, {"bg": "#333333"})
    assert st["bg"] == "#333333" and st["font"] == "Inter" and st["anim"] == "glow"
    kw = looks.visual_kwargs(st)
    assert kw["bg"] == "#333333" and kw["font"] == "Inter"


def test_empty_values_inherit():
    st = looks.merged({"accent": "#ff0000"}, {"accent": ""}, {"accent": None})
    assert st["accent"] == "#ff0000"


def test_animation_precedence():
    base = looks.merged()
    assert looks.animation_for(base, active=True, widget_anim=None) == "glow"       # default for "on" toggles
    assert looks.animation_for(base, active=False, widget_anim="float") == "float"  # widget suggestion
    explicit = looks.merged({"anim": "none"})
    assert looks.animation_for(explicit, active=False, widget_anim="float") is None  # user's "none" wins
    assert looks.animation_for(explicit, active=False, widget_anim="alert") == "alert"  # attention always wins
    only_active = looks.merged({"anim": "rainbow", "anim_when": "active"})
    assert looks.animation_for(only_active, active=False, widget_anim=None) is None
    assert looks.animation_for(only_active, active=True, widget_anim=None) == "rainbow"


@pytest.mark.parametrize("anim", list(ANIMATIONS))
def test_every_animation_renders(anim):
    for t in (0.0, 0.3, 0.7):
        img = render(Visual(icon="bell", label="x", anim=anim, anim_t=t), 112, 112)
        assert img.width() == 112


def test_fonts_gradients_and_shapes_render():
    for grad in ("diag", "vertical", "horizontal", "radial"):
        v = Visual(bg="#123456", bg2="#654321", grad=grad, font="JetBrains Mono", font_scale=1.4, radius=0,
                   border="none", sheen=False, value="12", sub="x", icon="cpu", icon_scale=1.3)
        assert not render(v, 176, 112).isNull()


def test_animated_gif_frames(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    frames = [Image.new("RGB", (64, 64), (i * 20, 0, 0)) for i in range(5)]
    path = tmp_path / "a.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    pic = picture(str(path), 112, 112, "cover")
    assert pic is not None and pic.animated and len(pic.frames) == 5 and pic.total_ms == 500
    assert pic.frame_at(0) == 0 and pic.frame_at(250) == 2 and pic.frame_at(520) == 0
    assert not render(Visual(image=str(path), frame=3), 112, 112).isNull()


def test_notify_center_lifecycle():
    c = NotifyCenter()
    assert c.apply("claude", "working") is False
    assert c.get("claude").status == "working"
    assert c.apply("claude", "done", "Refactor finished") is True
    st = c.get("claude")
    assert st.status == "done" and st.unseen and st.message == "Refactor finished"
    assert c.pending() == ["claude"]
    c.ack("claude")
    assert not c.get("claude").unseen and c.pending() == []
    c.apply("codex", "progress", progress=0.4)
    assert c.get("codex").status == "working" and c.get("codex").progress == 0.4


def test_old_profiles_get_new_pages_once():
    old = build_default_profile("pt")
    old.pages = [p for p in old.pages if p.id in ("daily", "windows", "assetto", "msfs", "msfs2")]
    old.auto_switch = [r for r in old.auto_switch if r["page"] in ("assetto", "msfs")]
    old.page("assetto").effects = ["ac_shift_lights"]
    data = old.to_dict()
    data.pop("seen_presets")                     # a profile saved by 0.1
    prof = Profile.from_dict(data)
    assert add_new_presets(prof, "pt") is True
    ids = [p.id for p in prof.pages]
    assert ids[-3:] == ["ai", "ets2", "beamng"]
    assert "rgb_shift_lights" in prof.page("assetto").effects
    assert any(r["process"] == "eurotrucks2.exe" for r in prof.auto_switch)
    # a page the user deleted later is not brought back
    prof.pages = [p for p in prof.pages if p.id != "ets2"]
    assert add_new_presets(prof, "pt") is False
    assert prof.page("ets2") is None


def test_http_bridge_refuses_web_pages():
    import json
    import urllib.error
    import urllib.request

    from freedock.notify import HttpBridge

    class FakeEngine:
        def __init__(self):
            from freedock.notify import NotifyCenter
            self.notify_center = NotifyCenter()
            self.events = []

        def call_in_ui(self, fn):
            fn()

        def notify_event(self, *a):
            self.events.append(a)

    eng = FakeEngine()
    bridge = HttpBridge(eng, 0)
    bridge.port = 47899
    bridge.start()
    try:
        with urllib.request.urlopen("http://127.0.0.1:47899/notify?source=x&event=done&message=hi", timeout=3) as r:
            assert json.load(r)["ok"] is True
        assert eng.events == [("x", "done", "hi", None)]
        req = urllib.request.Request("http://127.0.0.1:47899/notify?source=x&event=done",
                                     headers={"Origin": "https://evil.example"})
        with pytest.raises(urllib.error.HTTPError) as err:
            urllib.request.urlopen(req, timeout=3)
        assert err.value.code == 403 and len(eng.events) == 1
    finally:
        bridge.stop()


def test_idle_screen_renders_every_tile():
    import time
    from freedock.render import idle
    now = time.strptime("2026-11-06 10:31:07", "%Y-%m-%d %H:%M:%S")
    for info in (idle.IdleInfo(now=now, lang="pt", weather={"temp": 23.0, "icon": "cloud-sun"}, cpu=12, ram=58),
                 idle.IdleInfo(now=now, lang="en", alerts=[("#22c55e", "sparkles", "Claude: done")])):
        ks = idle.keys(info, 12.0, (112, 112))
        zs = idle.zones(info, 12.0, (176, 112))
        assert len(ks) == 10 and len(zs) == 4
        assert all(k.width() == 112 for k in ks) and all(z.width() == 176 for z in zs)
