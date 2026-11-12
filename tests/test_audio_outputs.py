"""Audio output switch: naming, skipping virtual devices, cycling, the action, the widget and the upgrade."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from freedock import audio_outputs as ao  # noqa: E402
from freedock.audio_outputs import Output  # noqa: E402

# what this author's PC reports (Windows endpoint name + adapter)
DEVICES = [
    Output("oculus", "Headphones (Oculus Virtual Audio Device)", "Headphones", "Oculus Virtual Audio Device"),
    Output("jbl", "JBL LED BRANCO (Realtek(R) Audio)", "JBL LED BRANCO", "Realtek(R) Audio"),
    Output("mon", "MONITOR ESQUERDA (NVIDIA High Definition Audio)", "MONITOR ESQUERDA", "NVIDIA High Definition Audio"),
    Output("steam", "Speakers (Steam Streaming Speakers)", "Speakers", "Steam Streaming Speakers"),
    Output("a50", "Headphones (5- Astro A50 Game)", "Headphones", "5- Astro A50 Game"),
    Output("cable", "CABLE Input (VB-Audio Virtual Cable)", "CABLE Input", "VB-Audio Virtual Cable"),
]


def test_the_name_that_tells_devices_apart_goes_big():
    by = {o.id: o for o in DEVICES}
    assert ao.title(by["jbl"]) == ("JBL LED BRANCO", "Realtek Audio")          # the user's own name wins
    assert ao.title(by["mon"]) == ("MONITOR ESQUERDA", "NVIDIA HD Audio")
    assert ao.title(by["a50"]) == ("Astro A50 Game", "Headphones")              # generic name -> adapter
    assert ao.title(Output("x", "Speakers (Realtek(R) Audio)")) == ("Speakers", "Realtek Audio")


def test_split_name_keeps_nested_parentheses():
    assert ao.split_name("JBL (Realtek(R) Audio)") == ("JBL", "Realtek(R) Audio")
    assert ao.split_name("Plain") == ("Plain", "")


def test_icons_follow_the_device_kind():
    by = {o.id: o for o in DEVICES}
    assert ao.icon_for(by["a50"]) == "headphones"
    assert ao.icon_for(by["mon"]) == "monitor-speaker"
    assert ao.icon_for(by["jbl"]) == "speaker"
    assert ao.icon_for(Output("dp", "DP (NVIDIA High Definition Audio)")) == "monitor-speaker"


def test_virtual_devices_are_skipped_but_the_current_one_stays_listed():
    ids = [o.id for o in ao.usable(DEVICES)]
    assert ids == ["a50", "jbl", "mon"]                                     # sorted by the big name
    assert "cable" in [o.id for o in ao.usable(DEVICES, keep="cable")]
    assert [o.id for o in ao.usable(DEVICES, skip="")] == ["a50", "cable", "jbl", "mon", "oculus", "steam"]


def test_step_cycles_both_ways_and_wraps():
    assert ao.step(DEVICES, "jbl").id == "mon"
    assert ao.step(DEVICES, "mon").id == "a50"                              # wraps around
    assert ao.step(DEVICES, "jbl", -1).id == "a50"
    assert ao.step(DEVICES, "cable").id == "jbl"                            # leaves a skipped default
    assert ao.step(DEVICES, "unknown").id == "a50"
    assert ao.step([DEVICES[1]], "jbl") is None                             # nothing else to go to
    assert ao.position(DEVICES, "mon") == (3, 3)


def test_find_by_name():
    assert ao.find(DEVICES, "astro").id == "a50"
    assert ao.find(DEVICES, "JBL LED BRANCO").id == "jbl"
    assert ao.find(DEVICES, "") is None


def test_pactl_sinks_parse():
    from freedock.platform.linux import parse_pactl_sinks
    text = """Sink #47
\tState: RUNNING
\tName: alsa_output.pci-0000_00_1f.3.analog-stereo
\tDescription: Built-in Audio Analog Stereo
Sink #52
\tName: bluez_output.AA_BB.1
\tDescription: WH-1000XM4
"""
    sinks = parse_pactl_sinks(text)
    assert [(s.id, s.name) for s in sinks] == [("alsa_output.pci-0000_00_1f.3.analog-stereo",
                                                 "Built-in Audio Analog Stereo"), ("bluez_output.AA_BB.1", "WH-1000XM4")]


class FakeEngine:
    def __init__(self):
        self.calls = []
        self.audio = {"level": 0.42, "muted": False}

    def output_switching(self, target):
        self.calls.append(("switching", target.id))

    def output_switched(self, target, ok):
        self.calls.append(("switched", target.id, ok))

    def call_in_ui(self, fn):
        fn()


def test_action_switches_to_the_next_real_device(monkeypatch):
    from freedock import actions
    switched = []
    monkeypatch.setattr(actions.osb, "audio_outputs", lambda: (DEVICES, "jbl"), raising=False)
    monkeypatch.setattr(actions.osb, "set_default_output", lambda i, comms=False: switched.append((i, comms)) or True,
                        raising=False)
    eng = FakeEngine()
    actions.REGISTRY["audio_output"].run(eng, {"type": "audio_output", "op": "next"})
    assert switched == [("mon", False)]
    assert eng.calls == [("switching", "mon"), ("switched", "mon", True)]
    actions.REGISTRY["audio_output"].run(eng, {"type": "audio_output", "op": "set", "device": "astro", "comms": True})
    assert switched[-1] == ("a50", True)
    actions.REGISTRY["audio_output"].run(eng, {"type": "audio_output", "op": "set", "device": "JBL LED BRANCO"})
    assert len(switched) == 2                                               # already the default: nothing to do


def test_widget_shows_the_default_device_and_its_place():
    from freedock.render import Visual
    from freedock.widgets import WIDGETS

    class E(FakeEngine):
        def audio_outputs(self):
            return DEVICES, "a50"

    v = WIDGETS["audio_output"].fn(E(), None, {"type": "audio_output"}, Visual())
    assert (v.value, v.sub, v.icon, v.badge) == ("Astro A50 Game", "Headphones", "headphones", "1/3")
    assert v.progress == 0.42


def test_daily_brightness_slots_become_the_output_switch_once():
    from freedock.presets import add_new_presets, build_default_profile, daily
    from freedock.profile import Knob, Profile, Slot
    prof = build_default_profile("pt")
    page = prof.page("daily")
    assert page.zones[1].widget == {"type": "audio_output"}
    assert page.knobs[1].right == {"type": "audio_output", "op": "next"}
    # a 0.3 profile: stock brightness zone + knob
    old = Profile.from_dict(prof.to_dict())
    old.seen_presets.remove("daily+audio_out")
    od = old.page("daily")
    od.zones[1] = Slot(label="Brilho", icon="sun", action={"type": "deck", "op": "screensaver"})
    od.knobs[1] = Knob(left={"type": "brightness", "op": "down", "step": 5},
                       right={"type": "brightness", "op": "up", "step": 5})
    assert add_new_presets(old, "pt") is True
    assert od.zones[1].widget == {"type": "audio_output"} and od.knobs[1].left["type"] == "audio_output"
    # an edited zone is the user's: kept
    mine = Profile.from_dict(prof.to_dict())
    mine.seen_presets.remove("daily+audio_out")
    mine.page("daily").zones[1] = Slot(label="Meu botão", icon="sun", action={"type": "deck", "op": "screensaver"})
    add_new_presets(mine, "pt")
    assert mine.page("daily").zones[1].label == "Meu botão"
    assert daily("en").zones[1].widget["type"] == "audio_output"
