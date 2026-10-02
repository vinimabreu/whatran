from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "examples"))

import make_demo  # noqa: E402

DAY = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)


def make_db(path: Path, rows: list[dict], *, schema: str = "current") -> Path:
    """An atuin database with the given rows; ``schema="2023"`` stops before author columns."""
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    statements = make_demo.MIGRATIONS if schema == "current" else make_demo.MIGRATIONS[:3]
    for statement in statements:
        conn.execute(statement)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(history)")]
    for i, row in enumerate(rows):
        base = {
            "id": f"id{i}", "timestamp": int((DAY + timedelta(minutes=i)).timestamp() * 1e9),
            "duration": 1_000_000_000, "exit": 0, "command": "ls", "cwd": "/Users/dev/code/app",
            "session": "s1", "hostname": "laptop:dev",
        }
        base.update(row)
        data = {k: v for k, v in base.items() if k in cols}
        conn.execute(f"insert into history ({','.join(data)}) values ({','.join('?' * len(data))})",
                     list(data.values()))
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def db(tmp_path):
    def _make(rows, **kw):
        return make_db(tmp_path / "history.db", rows, **kw)
    return _make


@pytest.fixture(autouse=True)
def isolated_home(tmp_path_factory, monkeypatch):
    """No test reads the real Claude Code sessions or atuin history of whoever runs it."""
    fake = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(fake / ".claude"))
    monkeypatch.setenv("XDG_DATA_HOME", str(fake / "share"))
    monkeypatch.delenv("ATUIN_DB_PATH", raising=False)
