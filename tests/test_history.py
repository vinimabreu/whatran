from __future__ import annotations

from datetime import timedelta

import pytest
from conftest import DAY

from whatran import history


def load(path, **kw):
    with history.connect(path) as conn:
        return history.load(conn, DAY - timedelta(hours=1), **kw)


def test_reads_rows_in_order_with_units_converted(db):
    path = db([{"command": "git status", "duration": 2_500_000_000, "exit": 0},
               {"command": "bun test", "exit": 1}])
    rows = load(path)
    assert [r.command for r in rows] == ["git status", "bun test"]
    assert rows[0].duration_ms == 2500
    assert rows[1].exit == 1
    assert rows[0].when == DAY


def test_deleted_rows_and_rows_outside_the_window_are_left_out(db):
    path = db([{"command": "kept"}, {"command": "deleted", "deleted_at": 1},
               {"command": "too early", "timestamp": int((DAY - timedelta(days=1)).timestamp() * 1e9)}])
    assert [r.command for r in load(path)] == ["kept"]
    with history.connect(path) as conn:
        assert history.load(conn, DAY + timedelta(minutes=5)) == []
        assert history.load(conn, DAY - timedelta(hours=1), DAY) == []


def test_database_from_before_author_columns_still_loads_as_yours(db):
    path = db([{"command": "ls"}], schema="2023")
    (row,) = load(path)
    assert row.author is None and row.author_kind is None
    assert not row.is_agent
    assert row.who == "you"


def test_the_file_is_opened_read_only(db):
    path = db([{"command": "ls"}])
    with history.connect(path) as conn:
        with pytest.raises(Exception, match="readonly"):
            conn.execute("delete from history")


def test_missing_file_and_wrong_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        history.connect(tmp_path / "nope.db")
    other = tmp_path / "other.db"
    import sqlite3
    sqlite3.connect(other).execute("create table history (x)").connection.commit()
    with history.connect(other) as conn, pytest.raises(ValueError):
        history.load(conn, DAY)


@pytest.mark.parametrize("author, kind, origin, agent", [
    ("claude-code", 2, "laptop:dev", True),         # stated agent
    ("claude-code", 1, "laptop:dev", False),        # stated user wins over the name
    ("anything", 2, "laptop:dev", True),            # stated agent with an unknown name
    ("codex", None, "laptop:dev", True),            # no kind: a known agent name
    ("dev", None, "laptop:dev", False),             # no kind: the default author
    ("pi", None, "raspberry:pi", False),            # the user called pi is not the pi agent
    ("pi", None, "raspberry:ellie", True),
    ("pi", None, "pi:unknown-user", False),         # legacy origin: compare with the host
    ("my-bot", None, "laptop:dev", False),          # unknown name and no kind: atuin says user
    (None, None, "laptop:dev", False),
])
def test_agent_detection_matches_atuin(db, author, kind, origin, agent):
    path = db([{"author": author, "author_kind": kind, "hostname": origin}])
    (row,) = load(path)
    assert row.is_agent is agent


def test_unrecognised_kind_falls_back_to_the_name(db):
    path = db([{"author": "codex", "author_kind": 7}])
    (row,) = load(path)
    assert row.author_kind is None and row.is_agent


def test_default_path_respects_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ATUIN_DB_PATH", str(tmp_path / "x.db"))
    assert history.default_db_path() == tmp_path / "x.db"
    monkeypatch.delenv("ATUIN_DB_PATH")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    assert history.default_db_path() == tmp_path / "atuin" / "history.db"
