"""Counter-Strike 2 game state integration: config file, state folding, bomb timer, events, HTTP."""
import json
import os
import threading
import urllib.error
import urllib.request

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from freedock.integrations import cs2  # noqa: E402
from freedock.integrations.cs2 import Cs2State, demo_payloads  # noqa: E402


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _run_demo(seconds: float, step: float = 0.1):
    clock = Clock()
    s = Cs2State(clock=clock)
    events, cursor = [], 0
    for i in range(int(seconds / step)):
        clock.t = i * step
        s.update(demo_payloads(clock.t))
        cursor, new = s.events_since(cursor)
        events += [(round(t, 1), n) for t, n, _ in new]
    return s, clock, events


def test_cfg_file_points_at_freedock_with_its_token(tmp_path):
    text = cs2.cfg_text(47812, "secret")
    assert '"uri"        "http://127.0.0.1:47812/"' in text and '"token"  "secret"' in text
    assert '"phase_countdowns"    "1"' in text
    written = cs2.install_cfg(47812, "secret", [tmp_path])
    assert written == [tmp_path / cs2.CFG_NAME] and (tmp_path / cs2.CFG_NAME).read_text(encoding="utf-8") == text
    assert cs2.install_cfg(47812, "secret", [tmp_path]) == []              # up to date: untouched
    assert cs2.install_cfg(47812, "other", [tmp_path]) == [tmp_path / cs2.CFG_NAME]


def test_a_scripted_round_raises_the_right_events():
    _s, _c, events = _run_demo(66)
    assert events == [(8.0, "round_start"), (14.0, "kill"), (19.0, "headshot"), (24.0, "flashed"), (27.0, "hurt"),
                      (30.0, "bomb_planted"), (62.0, "bomb_defused"), (62.0, "round_won")]


def test_bomb_timer_uses_phase_countdowns_and_knows_the_defuse():
    s, clock, _ = _run_demo(45)                    # planted at 30 with a 40 s countdown
    d = s.snapshot()
    assert d["bomb"] == "planted" and abs(d["bomb_left"] - 25.1) < 0.2
    assert d["can_defuse"] is True and d["defuse_need"] == cs2.DEFUSE_KIT_S
    clock.t += 3                                   # no post for 3 s: the timer keeps running locally
    assert abs(s.snapshot()["bomb_left"] - 22.1) < 0.2
    s2, _, _ = _run_demo(58)
    assert s2.snapshot()["defusing"] is True


def _payload(steamid="me", bomb=None, health=100, team="CT", kit=False, kills=0, rnd=3, countdown=None):
    p = {"provider": {"steamid": "me"}, "map": {"name": "de_inferno", "phase": "live", "round": rnd,
                                                "team_ct": {"score": 1}, "team_t": {"score": 2}},
         "round": {"phase": "live", **({"bomb": bomb} if bomb else {})},
         "player": {"steamid": steamid, "team": team, "state": {"health": health, "defusekit": kit,
                                                                "round_kills": kills, "round_killhs": 0}}}
    if countdown:
        p["phase_countdowns"] = countdown
    return p


def test_local_bomb_clock_without_countdowns_and_no_kit_verdict():
    clock = Clock()
    s = Cs2State(clock=clock)
    s.update(_payload())
    s.update(_payload(bomb="planted"))
    clock.t = 31.0                                 # 9 s left: a CT without a kit needs 10
    s.update(_payload(bomb="planted"))             # (heartbeat: the game is still there)
    d = s.snapshot()
    assert abs(d["bomb_left"] - 9.0) < 1e-6 and d["defuse_need"] == cs2.DEFUSE_S and d["can_defuse"] is False
    s.update(_payload(bomb="planted", kit=True))
    assert s.snapshot()["can_defuse"] is True      # picked up a kit: 5 s is enough
    s.update(_payload(bomb="exploded"))
    assert s.snapshot()["bomb_left"] is None
    assert [n for _t, n, _ in s.events_since(0)[1]] == ["bomb_planted", "bomb_exploded"]


def test_spectated_players_do_not_raise_personal_events():
    s = Cs2State(clock=Clock())
    s.update(_payload())
    s.update(_payload(steamid="teammate", kills=0))
    s.update(_payload(steamid="teammate", kills=2, health=0))
    names = [n for _t, n, _ in s.events_since(0)[1]]
    assert "kill" not in names and "death" not in names
    assert s.snapshot()["spectating"] is True and s.snapshot()["team"] == "CT"


def test_death_and_stale_game():
    clock = Clock()
    s = Cs2State(clock=clock)
    s.update(_payload())
    s.update(_payload(health=0))
    assert "death" in [n for _t, n, _ in s.events_since(0)[1]]
    clock.t = cs2.STALE_S + 1
    assert s.snapshot() == {"connected": False}


def test_weapon_names():
    assert cs2.weapon_name("weapon_ak47") == "AK-47"
    assert cs2.weapon_name("weapon_knife_karambit") == "Knife"
    assert cs2.weapon_name("weapon_new_thing") == "New Thing"


def test_http_receiver_checks_the_token():
    from http.server import ThreadingHTTPServer
    state = Cs2State()
    handler = type("H", (cs2._Handler,), {"state": state, "token": "tok"})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def post(body):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST")
        try:
            return opener.open(req, timeout=3).status
        except urllib.error.HTTPError as exc:
            return exc.code
    try:
        assert post({**_payload(), "auth": {"token": "wrong"}}) == 403
        assert state.snapshot() == {"connected": False}
        assert post({**_payload(), "auth": {"token": "tok"}}) == 200
        assert state.snapshot()["map"] == "de_inferno"
    finally:
        server.shutdown()
        server.server_close()


def test_bomb_widget():
    from freedock.render import Visual
    from freedock.widgets import WIDGETS
    s, _, _ = _run_demo(45)

    class E:
        def cs2(self):
            return s.snapshot()

        def smooth(self, ref, name, target, speed=0.25):
            return target
    v = WIDGETS["cs2"].fn(E(), None, {"type": "cs2", "metric": "bomb"}, Visual())
    assert v.icon == "bomb" and v.value == "25" and v.accent == "#22c55e" and v.anim == "pulse"
    hp = WIDGETS["cs2"].fn(E(), None, {"type": "cs2", "metric": "health"}, Visual())
    assert hp.value == "22" and hp.anim == "heartbeat"
