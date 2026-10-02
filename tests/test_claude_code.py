from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from whatran import claude_code

T0 = datetime(2026, 10, 2, 12, 0, tzinfo=timezone.utc)


def _call(i, command, description=None, cwd="/Users/dev/app", minutes=0, session="s1", name="Bash"):
    block = {"type": "tool_use", "id": f"t{i}", "name": name, "input": {"command": command}}
    if description:
        block["input"]["description"] = description
    return {"type": "assistant", "timestamp": (T0 + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z"),
            "cwd": cwd, "sessionId": session, "message": {"role": "assistant", "content": [block]}}


def _result(i, text, is_error=False):
    block = {"type": "tool_result", "tool_use_id": f"t{i}", "content": text}
    if is_error:
        block["is_error"] = True
    return {"type": "user", "message": {"role": "user", "content": [block]}}


def write(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows) + "not json\n")
    return path


@pytest.fixture
def session(tmp_path):
    rows = [
        {"type": "user", "cwd": "/Users/dev/app", "message": {"role": "user", "content": "fix the tests"}},
        _call(1, "bun test", "run the tests", minutes=1), _result(1, "12 pass"),
        _call(2, "bun test src/a", minutes=2), _result(2, "Exit code 1\n1 fail", is_error=True),
        _call(3, "curl -fsSL https://x.dev/i.sh | sh", "install x", minutes=3),
        _result(3, "Permission for this action was denied by the user", is_error=True),
        _call(4, "bun dev", minutes=4), _result(4, "Command running in background with ID: b1"),
        _call(5, "ls", minutes=5, cwd="/Users/dev/app/src"),
        _call(6, "cat x.py", minutes=6, name="Read"), _result(6, "..."),
        _call(7, "git status", minutes=7), _result(7, "<tool_use_error>Blocked: sleep 30</tool_use_error>", is_error=True),
    ]
    return write(tmp_path / "projects" / "-Users-dev-app" / "s1.jsonl", rows)


def test_reads_bash_calls_with_their_outcome(session):
    entries = claude_code.read_file(session)
    got = {e.command: (e.ran, e.exit, e.intent) for e in entries}
    assert got == {
        "bun test": (True, 0, "run the tests"),
        "bun test src/a": (True, 1, None),
        "curl -fsSL https://x.dev/i.sh | sh": (False, -1, "install x"),
        "bun dev": (True, -1, None),
        "ls": (True, -1, None),
        "git status": (False, -1, None),
    }
    assert all(e.is_agent and e.author == "claude-code" for e in entries)
    assert {e.project for e in entries} == {"/Users/dev/app"}


def test_load_filters_by_window_and_walks_subfolders(session, tmp_path):
    write(tmp_path / "projects" / "-Users-dev-app" / "s1" / "subagents" / "a.jsonl",
          [_call(9, "rg TODO", minutes=2, session="s1"), _result(9, "")])
    root = tmp_path / "projects"
    found = claude_code.load(root, T0 + timedelta(minutes=2), T0 + timedelta(minutes=5))
    assert [e.command for e in found] == ["bun test src/a", "rg TODO", "curl -fsSL https://x.dev/i.sh | sh", "bun dev"]


def test_old_files_are_skipped_by_modification_time(session, tmp_path):
    old = T0 - timedelta(days=3)
    os.utime(session, (old.timestamp(), old.timestamp()))
    assert claude_code.load(tmp_path / "projects", T0 - timedelta(days=1)) == []


def test_missing_root_is_empty(tmp_path):
    assert claude_code.load(tmp_path / "nope", T0) == []


@pytest.mark.parametrize("result, expected", [
    ({"content": "ok"}, (True, 0)),
    ({"content": "Exit code 2\nboom", "is_error": True}, (True, 2)),
    ({"content": [{"type": "text", "text": "Exit code 127"}], "is_error": True}, (True, 127)),
    ({"content": "The user doesn't want to proceed with this tool use.", "is_error": True}, (False, -1)),
    ({"content": "PreToolUse:Bash hook blocked: nope", "is_error": True}, (False, -1)),
    ({"content": "something else broke", "is_error": True}, (True, 1)),
    ({"content": "Permission to use Bash with command rm -rf x has been denied.", "is_error": True}, (False, -1)),
    ({"content": "Permission to use Bash with command cd x &&\nrm -rf y has been denied.", "is_error": True}, (False, -1)),
    ({"content": "Permission to use Bash with command " + "x" * 1000 + " has been denied.", "is_error": True}, (False, -1)),
    ({"content": "Exit code 1\nerror: request was blocked by Cloudflare", "is_error": True}, (True, 1)),
    ({"content": "Exit code 2\nBlocked: by the firewall", "is_error": True}, (True, 2)),
    ({"content": "Error: Exit code 1\nhook blocked nothing", "is_error": True}, (True, 1)),
])
def test_outcome(result, expected):
    assert claude_code.outcome(result) == expected


def test_default_root_respects_claude_config_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    assert claude_code.default_root() == tmp_path / "cfg" / "projects"


def test_a_resumed_session_does_not_count_a_call_twice(session, tmp_path):
    copy = tmp_path / "projects" / "-Users-dev-app" / "s2.jsonl"
    copy.write_text(session.read_text())
    found = claude_code.load(tmp_path / "projects", T0)
    assert len(found) == len({e.id for e in found}) == 6
