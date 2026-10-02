from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from conftest import make_db

import make_demo
from whatran import cli, model


@pytest.mark.parametrize("host, local", [
    ("http://127.0.0.1:11434", True), ("localhost:11434", True), ("http://[::1]:11434", True),
    ("127.0.0.1", True), ("http://192.168.0.10:11434", False), ("https://ollama.example.com", False),
])
def test_loopback(host, local):
    assert model.is_loopback(host) is local


def test_remote_host_is_refused_before_anything_is_sent(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("tried to connect")
    monkeypatch.setattr(model.urllib.request, "urlopen", boom)
    with pytest.raises(model.RemoteHostRefused):
        model.chat("s", "u", host="http://10.0.0.5:11434")


def test_unreachable_ollama_is_reported(monkeypatch):
    def refused(*a, **k):
        raise model.urllib.error.URLError("refused")
    monkeypatch.setattr(model.urllib.request, "urlopen", refused)
    with pytest.raises(model.ModelUnavailable, match="ollama serve"):
        model.chat("s", "u")


NOW = datetime(2026, 10, 2, 18, 0).astimezone()


@pytest.fixture
def demo(tmp_path):
    path = tmp_path / "history.db"
    make_demo.build(path, NOW)
    return path


def test_cli_without_model_on_the_demo_day(demo, capsys):
    assert cli.main(["--db", str(demo), "--no-model"], now=NOW) == 0
    out = capsys.readouterr().out
    assert "29 commands: 7 yours, 22 by agents (claude-code 17, codex 5)." in out
    assert "Worth a look (8)" in out
    assert "ghp_" not in out
    assert "curl -fsSL https://example-cli.dev/install.sh | sh" in out


def test_cli_all_flags_your_commands_too(demo, capsys):
    cli.main(["--db", str(demo), "--no-model", "--all"], now=NOW)
    assert "Worth a look (9)" in capsys.readouterr().out


def test_cli_shows_a_checked_note_and_withholds_a_bad_one(demo, capsys, monkeypatch):
    replies = iter(["[F1] It ran `cat .env`.", "Agents ran `rm -rf /` 99 times.", "Still `rm -rf /`."])
    monkeypatch.setattr(model, "chat", lambda *a, **k: next(replies))
    cli.main(["--db", str(demo)], now=NOW)
    assert "[F1] It ran `cat .env`." in capsys.readouterr().out
    cli.main(["--db", str(demo)], now=NOW)
    out = capsys.readouterr().out
    assert "was not shown" in out and "rm -rf /" in out.split("was not shown")[1]


def test_cli_without_ollama_still_prints_the_facts(demo, capsys, monkeypatch):
    def down(*a, **k):
        raise model.ModelUnavailable("Ollama is not reachable at x; start it with: ollama serve")
    monkeypatch.setattr(model, "chat", down)
    assert cli.main(["--db", str(demo)], now=NOW) == 0
    out = capsys.readouterr().out
    assert "Worth a look (8)" in out and "no note: Ollama is not reachable" in out


def test_cli_json(demo, capsys):
    cli.main(["--db", str(demo), "--no-model", "--json"], now=NOW)
    data = json.loads(capsys.readouterr().out)
    assert data["facts"]["by_agents"] == 22 and data["note"] is None


def test_cli_missing_db(tmp_path, capsys):
    assert cli.main(["--db", str(tmp_path / "none.db"), "--no-model"], now=NOW) == 2
    assert "no atuin database" in capsys.readouterr().err


@pytest.mark.parametrize("text, expected", [
    ("today", datetime(2026, 10, 2, 0, 0)), ("24h", datetime(2026, 10, 1, 18, 0)),
    ("3d", datetime(2026, 9, 29, 18, 0)), ("1w", datetime(2026, 9, 25, 18, 0)),
    ("2026-10-01", datetime(2026, 10, 1, 0, 0)),
])
def test_parse_since(text, expected):
    assert cli.parse_since(text, NOW).replace(tzinfo=None) == expected


def test_parse_since_rejects_nonsense():
    with pytest.raises(Exception):
        cli.parse_since("yesterday-ish", NOW)


def test_old_schema_database_runs(tmp_path, capsys):
    path = make_db(tmp_path / "old.db", [{"command": "ls", "timestamp": int(NOW.astimezone(timezone.utc).timestamp() * 1e9) - 10**9}],
                   schema="2023")
    assert cli.main(["--db", str(path), "--no-model"], now=NOW) == 0
    assert "1 command: 1 yours, 0 by agents." in capsys.readouterr().out
