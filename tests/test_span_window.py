from freedock.integrations import span_window as sw

CFG = 'uset r_mode_width "{w}"\nuset r_mode_height "1080"\nuset r_fullscreen "{fs}"\n'
DESKTOP = (-1920, 0, 6400, 1080)       # 27" + 34" ultrawide + 27"


def test_windowed_desktop_size_spans_every_monitor():
    assert sw.target_rect(sw.wanted_size(CFG.format(w=6400, fs=0)), DESKTOP) == DESKTOP


def test_fullscreen_or_other_size_is_left_alone():
    assert sw.wanted_size(CFG.format(w=6400, fs=1)) is None
    assert sw.target_rect(sw.wanted_size(CFG.format(w=2560, fs=0)), DESKTOP) is None
    assert sw.wanted_size("") is None


def test_moves_only_the_shrunken_game_window(monkeypatch, tmp_path):
    (tmp_path / "Euro Truck Simulator 2").mkdir()
    (tmp_path / "Euro Truck Simulator 2" / "config.cfg").write_text(CFG.format(w=6400, fs=0))
    moved = []
    monkeypatch.setattr(sw.sys, "platform", "win32")
    monkeypatch.setattr(sw, "_desktop", lambda: DESKTOP)
    monkeypatch.setattr(sw, "_windows", lambda exe: [(1, 10, (0, 324, 2560, 432)), (1, 11, (0, 0, 116, 1)),
                                                     (1, 12, DESKTOP)])
    monkeypatch.setattr(sw, "_move", lambda hwnd, rect: moved.append((hwnd, rect)) or True)
    sp = sw.WindowSpanner(documents=tmp_path)
    sp.update({"eurotrucks2.exe", "explorer.exe"})
    assert moved == [(10, DESKTOP)]
    moved.clear()
    sp.update({"explorer.exe"})
    assert moved == []
