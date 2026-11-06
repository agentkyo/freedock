import json
import os
import tomllib
from datetime import datetime, timezone

import pytest

from freedock.integrations import ai

CMD = [r"C:\Program Files\FreeDock\freedock.exe"]
CODEX_PREV = [r"C:\Users\x\AppData\Local\OpenAI\Codex\runtimes\cua\codex-computer-use.exe", "turn-ended"]


# ---------------------------------------------------------------- Claude hooks

def _user_settings():
    return {
        "theme": "dark",
        "hooks": {
            "Stop": [{"hooks": [{"type": "command", "command": "say done"}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "guard.sh"}]}],
        },
    }


def test_claude_hooks_patch_adds_and_preserves():
    orig = _user_settings()
    new = ai.claude_hooks_patch(orig, CMD)
    assert orig == _user_settings()                       # input not mutated
    assert new["theme"] == "dark"
    assert new["hooks"]["PreToolUse"] == orig["hooks"]["PreToolUse"]
    assert new["hooks"]["Stop"][0] == orig["hooks"]["Stop"][0]
    ours = new["hooks"]["Stop"][1]["hooks"][0]
    assert ours["command"] == CMD[0] and ours["args"] == ["notify", "claude", "done"]
    assert ours["async"] is True
    assert new["hooks"]["UserPromptSubmit"][0]["hooks"][0]["args"][-1] == "working"
    notif = new["hooks"]["Notification"][0]
    assert "permission_prompt" in notif["matcher"] and "idle_prompt" not in notif["matcher"]
    assert notif["hooks"][0]["args"][-1] == "question"
    assert "matcher" not in new["hooks"]["Stop"][1]


def test_claude_hooks_patch_idempotent_and_remove():
    once = ai.claude_hooks_patch(_user_settings(), CMD)
    twice = ai.claude_hooks_patch(once, CMD)
    assert once == twice
    moved = ai.claude_hooks_patch(once, ["python", "-m", "freedock"])   # reinstall from elsewhere
    assert len(moved["hooks"]["Stop"]) == 2
    assert moved["hooks"]["Stop"][1]["hooks"][0]["args"] == ["-m", "freedock", "notify", "claude", "done"]
    assert ai.claude_hooks_remove(twice) == _user_settings()
    assert ai.claude_hooks_remove(ai.claude_hooks_patch({}, CMD)) == {}
    sub = ai.claude_hooks_patch({}, CMD, subagents=True)
    assert "SubagentStop" in sub["hooks"]


def test_claude_hook_payload_mapping():
    assert ai.claude_event_from_hook({"hook_event_name": "Notification", "notification_type": "permission_prompt",
                                      "message": "Claude needs your permission"}) == \
        ("question", "Claude needs your permission")
    assert ai.claude_event_from_hook({"hook_event_name": "Notification", "notification_type": "auth_success"}) is None
    assert ai.claude_event_from_hook(json.dumps({"hook_event_name": "Stop", "last_assistant_message": "ok"})) == \
        ("done", "ok")
    assert ai.claude_event_from_hook({"hook_event_name": "UserPromptSubmit"}) == ("working", None)


def test_statusline_patch_and_capture(tmp_path):
    new, prev = ai.claude_statusline_patch({"statusLine": {"type": "command", "command": "mine.sh"}}, CMD)
    assert prev == {"type": "command", "command": "mine.sh"}
    assert new["statusLine"]["command"].endswith(" statusline")
    assert ai.claude_statusline_remove(new, prev)["statusLine"] == prev
    cache = tmp_path / "limits.json"
    payload = {"model": {"display_name": "Opus"},
               "rate_limits": {"five_hour": {"used_percentage": 23.5, "resets_at": 1738425600},
                               "seven_day": {"used_percentage": 41.2, "resets_at": 1738857600}}}
    assert ai.claude_statusline_output(json.dumps(payload), None, cache) == "[Opus] 5h 24%"
    saved = json.loads(cache.read_text())
    assert saved["five_hour"]["used_percentage"] == 23.5 and saved["seven_day"]["resets_at"] == 1738857600


# ---------------------------------------------------------------- Claude 5h blocks

H = 3600


def test_claude_blocks_basic():
    t0 = 1_800_000_000 - 1_800_000_000 % H + 1200      # 20 min past an hour
    entries = [(t0, 100), (t0 + H, 50), (t0 + 2 * H, 25)]
    blk = ai.claude_blocks(entries, t0 + 3 * H)
    assert blk["start"] == t0 - 1200 and blk["end"] == t0 - 1200 + 5 * H
    assert blk["tokens"] == 175
    assert ai.claude_blocks(entries, t0 + 6 * H) is None                    # expired


def test_claude_blocks_rollover_and_gap():
    t0 = 1_800_000_000 - 1_800_000_000 % H
    # continuous activity: new block starts at the first message after 5h
    entries = [(t0 + i * 1800, 10) for i in range(12)]                      # 0h .. 5.5h
    blk = ai.claude_blocks(entries, t0 + 5.6 * H)
    assert blk["start"] == t0 + 5 * H and blk["tokens"] == 20
    # gap > 5h starts a fresh block at the next message's hour
    blk = ai.claude_blocks([(t0, 1), (t0 + 7 * H + 900, 5)], t0 + 7.5 * H)
    assert blk["start"] == t0 + 7 * H and blk["tokens"] == 5


def _iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")


def _assistant(ts, mid, req, out_tokens, model="claude-opus-4-1"):
    return json.dumps({"type": "assistant", "timestamp": _iso(ts), "requestId": req,
                       "message": {"id": mid, "model": model, "role": "assistant",
                                   "usage": {"input_tokens": 10, "output_tokens": out_tokens,
                                             "cache_creation_input_tokens": 100, "cache_read_input_tokens": 1000}}})


def test_claude_reader_dedup_and_incremental(tmp_path, monkeypatch):
    home = tmp_path / ".claude"
    proj = home / "projects" / "p"
    proj.mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home))
    monkeypatch.setattr(ai, "claude_limits_cache", lambda: tmp_path / "none.json")
    now = datetime.now().timestamp()
    f = proj / "s.jsonl"
    lines = [json.dumps({"type": "user", "timestamp": _iso(now - 600), "message": {"content": "hi"}}),
             _assistant(now - 500, "m1", "r1", 5),
             _assistant(now - 499, "m1", "r1", 20),          # same message, later block: final usage
             _assistant(now - 400, "m2", "r2", 30, "claude-sonnet-4-5")]
    f.write_text("\n".join(lines) + "\n")
    r = ai._ClaudeReader()
    d = r.read(now)
    per = lambda o: 10 + o + 100 + 1000
    assert d["tokens_5h"] == per(20) + per(30)
    assert d["model"] == "claude-sonnet-4-5"
    assert abs(d["last_activity"] - (now - 400)) < 1
    assert d["resets_at"] > now
    # append a partial line, then complete it
    with f.open("a") as fh:
        fh.write(_assistant(now - 100, "m3", "r3", 40)[:30])
    assert r.read(now)["tokens_5h"] == per(20) + per(30)
    with f.open("a") as fh:
        fh.write(_assistant(now - 100, "m3", "r3", 40)[30:] + "\n")
    os.utime(f, (now + 1, now + 1))
    assert r.read(now)["tokens_5h"] == per(20) + per(30) + per(40)


# ---------------------------------------------------------------- Codex

CONFIG = (
    'notify = [ "C:\\\\Users\\\\x\\\\AppData\\\\Local\\\\OpenAI\\\\Codex\\\\runtimes\\\\cua\\\\codex-computer-use.exe", '
    '"turn-ended" ]\n'
    "[desktop]\n"
    'followUpQueueMode = "steer"\n'
    "\n"
    "# keep me\n"
    "[mcp_servers.node_repl]\n"
    "args = []\n"
    "command = 'C:\\x\\node_repl.exe'\n"
)


def test_codex_notify_patch_round_trip():
    assert tomllib.loads(CONFIG)["notify"] == CODEX_PREV
    patched = ai.codex_notify_patch(CONFIG, CMD)
    parsed = tomllib.loads(patched)
    assert parsed["notify"] == [*CMD, "notify", "codex"]
    assert parsed["desktop"] == {"followUpQueueMode": "steer"}
    assert ai.codex_previous_notify(patched) == CODEX_PREV
    # everything after the notify line is byte-identical
    assert patched.split("[desktop]", 1)[1] == CONFIG.split("[desktop]", 1)[1]
    assert ai.codex_notify_patch(patched, CMD) == patched                     # idempotent
    moved = ai.codex_notify_patch(patched, ["py", "-m", "freedock"])
    assert tomllib.loads(moved)["notify"] == ["py", "-m", "freedock", "notify", "codex"]
    assert ai.codex_previous_notify(moved) == CODEX_PREV                      # not overwritten by ourselves
    restored = ai.codex_notify_remove(moved)
    assert tomllib.loads(restored) == tomllib.loads(CONFIG)
    assert restored.split("[desktop]", 1)[1] == CONFIG.split("[desktop]", 1)[1]
    assert ai.codex_notify_remove(CONFIG) == CONFIG


def test_codex_notify_patch_without_existing_and_multiline():
    base = 'model = "gpt-5"\r\n[tui]\r\nnotifications = true\r\n'
    p = ai.codex_notify_patch(base, CMD)
    assert tomllib.loads(p)["notify"] == [*CMD, "notify", "codex"] and "\r\n" in p
    assert ai.codex_previous_notify(p) is None
    assert ai.codex_notify_remove(p) == base
    multi = 'notify = [\n  "notify-send",\n  "Codex",\n]\nmodel = "o3"\n'
    p = ai.codex_notify_patch(multi, CMD)
    assert tomllib.loads(p) == {"notify": [*CMD, "notify", "codex"], "model": "o3"}
    assert ai.codex_previous_notify(p) == ["notify-send", "Codex"]
    assert tomllib.loads(ai.codex_notify_remove(p)) == tomllib.loads(multi)
    # notify inside a table is not the top-level key
    tabled = '[profiles.x]\nnotify = ["a"]\n'
    assert tomllib.loads(ai.codex_notify_patch(tabled, CMD))["profiles"]["x"]["notify"] == ["a"]


def test_codex_forward_refuses_loops():
    assert ai.codex_forward(None, "{}") is False
    assert ai.codex_forward([*CMD, "notify", "codex"], "{}") is False


def test_codex_payload_mapping():
    payload = {"type": "agent-turn-complete", "thread-id": "b5f6", "turn-id": "1", "cwd": "/p", "client": "codex-tui",
               "input-messages": ["Rename foo"], "last-assistant-message": "Rename complete."}
    assert ai.codex_event_from_payload(json.dumps(payload)) == ("done", "Rename complete.")
    assert ai.codex_event_from_payload({"type": "approval-requested", "command": ["rm", "x"]}) == ("question", "rm x")
    assert ai.codex_event_from_payload({"type": "something-new"}) is None
    assert ai.codex_event_from_payload("not json") is None


def test_codex_reader_rollout(tmp_path, monkeypatch):
    home = tmp_path / ".codex"
    day = home / "sessions" / "2026" / "11" / "05"
    day.mkdir(parents=True)
    monkeypatch.setenv("CODEX_HOME", str(home))
    now = datetime.now().timestamp()

    def tc(ts, total, pct5, pctw):
        return {"timestamp": _iso(ts), "type": "event_msg",
                "payload": {"type": "token_count",
                            "info": {"total_token_usage": {"total_tokens": total},
                                     "last_token_usage": {"total_tokens": 0}},
                            "rate_limits": {"primary": {"used_percent": pct5, "window_minutes": 300,
                                                        "resets_at": int(now + 3600)},
                                            "secondary": {"used_percent": pctw, "window_minutes": 10080,
                                                          "resets_at": int(now + 86400)}}}}
    rows = [{"timestamp": _iso(now - 300), "type": "turn_context", "payload": {"model": "gpt-5-codex"}},
            {"timestamp": _iso(now - 299), "type": "event_msg", "payload": {"type": "task_started"}},
            tc(now - 200, 1000, 10.0, 3.0), tc(now - 199, 1000, 10.0, 3.0), tc(now - 100, 1500, 12.5, 3.5),
            {"timestamp": _iso(now - 90), "type": "event_msg", "payload": {"type": "task_complete"}}]
    (day / "rollout-2026-11-05T10-00-00-abc.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    d = ai._CodexReader().read(now)
    assert d["tokens_5h"] == 1500 and d["tokens_today"] in (1500, 0)
    assert d["percent_5h"] == 12.5 and d["percent_week"] == 3.5
    assert abs(d["resets_at"] - (now + 3600)) < 2
    assert d["model"] == "gpt-5-codex" and d["status"] == "done"


# ---------------------------------------------------------------- OpenCode, status, launch

def test_opencode_plugin_and_tokens():
    src = ai.opencode_plugin_source(CMD)
    assert json.dumps(CMD) in src and "session.idle" in src and "permission.asked" in src
    assert ai.opencode_plugin_path().name == "freedock.js"
    msg = {"role": "assistant", "tokens": {"input": 10, "output": 5, "reasoning": 2, "cache": {"read": 100, "write": 3}}}
    assert ai.opencode_message_tokens(msg) == 120


def test_apply_event():
    s = ai.apply_event(None, "working", now=1)
    assert s["status"] == "working" and not s["alert"]
    s = ai.apply_event(s, "progress", progress=1.7, now=2)
    assert s["progress"] == 1.0 and s["since"] == 1
    s = ai.apply_event(s, "question", "Allow rm?", now=3)
    assert s["status"] == "needs_input" and s["alert"] and s["message"] == "Allow rm?"
    s = ai.apply_event(s, "done", now=4)
    assert s["alert"] and not ai.apply_event(s, "done", now=5)["alert"]
    with pytest.raises(ValueError):
        ai.apply_event(s, "bogus")


def test_launch_commands():
    win = ai.launch_commands("win32")
    assert set(win) == {"claude", "codex", "codex_app", "opencode"}
    assert win["codex_app"][1].startswith("shell:AppsFolder\\")
    lin = ai.launch_commands("linux")
    assert lin["claude"][-1] == "claude"
