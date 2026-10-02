"""Read an atuin history database, read-only, and tell agent commands from yours.

atuin keeps every shell command in a SQLite file. Since 2026 it also records who
ran each one: ``author_kind`` is 1 for a person and 2 for an agent when the
integration that captured the command said so, and ``author`` names the agent
(``claude-code``, ``codex``...). Older rows, and integrations that do not set
the kind, leave it empty; atuin then decides from the author name, and so does
this module, with the same exception atuin makes: an author that is only the
default it fell back to (the username in the row's origin) tells nothing.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

KNOWN_AGENTS = ("claude-code", "codex", "copilot", "opencode", "pi")
"""The names atuin itself treats as agents when no kind was stated."""

USER, AGENT = 1, 2
UNKNOWN_USER = "unknown-user"


@dataclass(frozen=True)
class Entry:
    id: str
    when: datetime
    duration_ms: int
    exit: int
    command: str
    cwd: str
    session: str
    origin: str
    author: str | None
    intent: str | None
    author_kind: int | None
    ran: bool = True
    """False when the command was blocked or denied before it could run."""
    project: str | None = None
    """The folder the agent session was started in, when the source records it."""

    @property
    def user(self) -> str:
        """The username half of the origin (``host:user``), as atuin reads it."""
        host, sep, user = self.origin.partition(":")
        if not sep:
            return self.origin
        return host if user == UNKNOWN_USER else user

    @property
    def is_agent(self) -> bool:
        if self.author_kind == AGENT:
            return True
        if self.author_kind == USER:
            return False
        return bool(self.author) and self.author in KNOWN_AGENTS and self.author != self.user

    @property
    def who(self) -> str:
        """A label for the reports: the agent's name, or ``you``."""
        if self.is_agent:
            return self.author or "an agent"
        return "you"


def default_db_path() -> Path:
    """Where atuin keeps the database unless its config says otherwise."""
    if os.environ.get("ATUIN_DB_PATH"):
        return Path(os.environ["ATUIN_DB_PATH"]).expanduser()
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return data / "atuin" / "history.db"


def connect(path: Path) -> sqlite3.Connection:
    """Open the database read-only. Nothing here ever writes to your history."""
    if not path.exists():
        raise FileNotFoundError(f"no atuin database at {path} (set --db or ATUIN_DB_PATH)")
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in conn.execute("PRAGMA table_info(history)")}


def _ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1_000_000_000)


def load(conn: sqlite3.Connection, since: datetime, until: datetime | None = None) -> list[Entry]:
    """Every command run in ``[since, until)``, oldest first, deleted ones left out.

    Databases written before atuin added ``author``, ``intent`` and ``author_kind``
    still load; those fields are just empty and every row counts as yours.
    """
    cols = _columns(conn)
    if "command" not in cols:
        raise ValueError("this file has no atuin history table")
    optional = [c if c in cols else f"NULL AS {c}" for c in ("author", "intent", "author_kind")]
    where = ["timestamp >= ?"]
    args: list[int] = [_ns(since)]
    if until is not None:
        where.append("timestamp < ?")
        args.append(_ns(until))
    if "deleted_at" in cols:
        where.append("deleted_at IS NULL")
    sql = (
        "SELECT id, timestamp, duration, exit, command, cwd, session, hostname, "
        + ", ".join(optional)
        + " FROM history WHERE " + " AND ".join(where) + " ORDER BY timestamp, id"
    )
    out = []
    for row in conn.execute(sql, args):
        kind = row["author_kind"]
        out.append(Entry(
            id=row["id"],
            when=datetime.fromtimestamp(row["timestamp"] / 1_000_000_000, tz=timezone.utc),
            duration_ms=max(0, int(row["duration"] or 0)) // 1_000_000,
            exit=int(row["exit"]),
            command=row["command"],
            cwd=row["cwd"],
            session=row["session"],
            origin=row["hostname"],
            author=row["author"] or None,
            intent=row["intent"] or None,
            author_kind=kind if kind in (USER, AGENT) else None,
        ))
    return out
