from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from conftest import make_db

import make_demo
from whatran import cli, model


@pytest.mark.parametrize("host, local", [
    ("http://127.0.0.1:11434", True), ("localhost:11434", True), ("http://[::1]:11434", True),
    ("127.0.0.1", True), ("http://192.168.0.10:11434", False), ("https://ollama.example.com", False),
    ("0.0.0.0", True), ("http://[::]:11434", True), ("127.1.2.3", True),
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
    good = "It ran `cat .env` [F1], then [F2] [F3] [F4] [F5] [F6] [F7] [F8]."
    replies = iter([good, "Agents ran `rm -rf /` 99 times.", "Still `rm -rf /`."])
    monkeypatch.setattr(model, "chat", lambda *a, **k: next(replies))
    cli.main(["--db", str(demo)], now=NOW)
    assert good in capsys.readouterr().out
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


def test_a_proxy_in_the_environment_is_never_used(monkeypatch):
    import http.server
    import threading
    hits = []

    class Proxy(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"message": {"content": "from the proxy"}}')

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Proxy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    proxy = f"http://127.0.0.1:{server.server_address[1]}"
    for var in ("http_proxy", "HTTP_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, proxy)
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    try:
        with pytest.raises(model.ModelUnavailable):
            model.chat("s", "u", host="http://127.0.0.1:9")  # nothing listens on port 9
    finally:
        server.shutdown()
    assert hits == []


def test_explicit_db_never_mixes_in_real_sessions(tmp_path, capsys, monkeypatch):
    claude = tmp_path / "cc" / "projects" / "p"
    claude.mkdir(parents=True)
    (claude / "s.jsonl").write_text(json.dumps({
        "type": "assistant",
        "timestamp": (NOW - timedelta(minutes=1)).astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "cwd": "/x", "sessionId": "s", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "secret-real-command"}}]}}) + "\n")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cc"))
    path = make_db(tmp_path / "old.db", [{"command": "ls", "timestamp": int(NOW.astimezone(timezone.utc).timestamp() * 1e9) - 10**9}])
    cli.main(["--db", str(path), "--no-model", "--json"], now=NOW)
    assert "secret-real-command" not in capsys.readouterr().out
    cli.main(["--no-model", "--json", "--source", "claude-code"], now=NOW)
    assert "secret-real-command" in capsys.readouterr().out


def test_not_a_database(tmp_path, capsys):
    bad = tmp_path / "x.db"
    bad.write_text("not sqlite at all " * 100)
    assert cli.main(["--db", str(bad), "--no-model"], now=NOW) == 2
    assert "not an atuin database" in capsys.readouterr().err


def test_refused_model_host_still_prints_the_facts(demo, capsys, monkeypatch):
    monkeypatch.setattr(model, "DEFAULT_HOST", "http://10.0.0.5:11434")
    monkeypatch.setattr(model.chat, "__kwdefaults__", {**model.chat.__kwdefaults__, "host": "http://10.0.0.5:11434"})
    assert cli.main(["--db", str(demo)], now=NOW) == 0
    out = capsys.readouterr().out
    assert "Worth a look (8)" in out and "is not this machine" in out


def test_since_accepts_iso_with_z():
    assert cli.parse_since("2026-10-02T10:00Z", NOW) == datetime(2026, 10, 2, 10, 0, tzinfo=timezone.utc)


def test_demo_day_is_always_already_over():
    morning = datetime(2026, 10, 2, 8, 30).astimezone()
    afternoon = datetime(2026, 10, 2, 15, 0).astimezone()
    assert make_demo.last_finished_day(morning).day == 1
    assert make_demo.last_finished_day(afternoon).day == 2


@pytest.mark.parametrize("hour, minute", [(0, 30), (9, 30), (11, 30), (12, 6), (12, 11), (18, 0)])
def test_the_readme_demo_command_shows_the_whole_day_at_any_hour(tmp_path, capsys, hour, minute):
    now = datetime(2026, 10, 2, hour, minute).astimezone()
    path = tmp_path / "history.db"
    make_demo.build(path, make_demo.last_finished_day(now))
    cli.main(["--db", str(path), "--since", "2d", "--no-model"], now=now)
    out = capsys.readouterr().out
    assert "22 by agents" in out and "Worth a look (8)" in out
