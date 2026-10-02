"""Build a demo atuin database with one made-up working day.

    python examples/make_demo.py demo/history.db

The schema is atuin's own, applied migration by migration, so whatran reads the
demo exactly as it reads a real history. Every command, path and token here is
invented; the tokens are shaped like real ones only so the redaction shows.
"""

from __future__ import annotations

import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

MIGRATIONS = (
    """create table if not exists history (
        id text primary key, timestamp integer not null, duration integer not null,
        exit integer not null, command text not null, cwd text not null,
        session text not null, hostname text not null, unique(timestamp, cwd, command))""",
    "create index if not exists idx_history_timestamp on history(timestamp)",
    "alter table history add column deleted_at integer",
    "alter table history add column author text",
    "alter table history add column intent text",
    "alter table history add column shell text",
    "alter table history add column author_kind integer",
)

HOME = "/Users/dev"
ORIGIN = "laptop:dev"
FAKE_GH = "ghp_" + "x7Qm2" * 7 + "a"

# (minutes after 09:00, who, author_kind, cwd, command, exit, seconds, intent)
DAY = [
    (0, "dev", 1, "~/code/checkout-api", "tmux attach -t work", 0, 1, None),
    (2, "dev", 1, "~/code/checkout-api", "git pull --rebase", 0, 2, None),
    (4, "dev", 1, "~/code/checkout-api", "bun dev", 0, 1, None),
    (11, "claude-code", 2, "~/code/checkout-api", "git status", 0, 0, None),
    (11, "claude-code", 2, "~/code/checkout-api", 'rg -n "applyDiscount" src', 0, 0, "find where discounts are rounded"),
    (12, "claude-code", 2, "~/code/checkout-api", "bun test src/pricing", 1, 4, "reproduce the rounding bug"),
    (14, "claude-code", 2, "~/code/checkout-api", "cat .env", 0, 0, "check which payment key the tests read"),
    (15, "claude-code", 2, "~/code/checkout-api", "bun test src/pricing", 1, 4, None),
    (18, "claude-code", 2, "~/code/checkout-api", "bun test src/pricing", 0, 4, "confirm the fix"),
    (19, "claude-code", 2, "~/code/checkout-api", "git diff --stat", 0, 0, None),
    (21, "claude-code", 2, "~/code/checkout-api", "rm -rf node_modules dist && bun install", 0, 9, "clean reinstall after the lockfile conflict"),
    (23, "claude-code", 2, "~/code/checkout-api", "rm -rf migrations && bun run db:generate", 0, 3, "regenerate the migrations from the schema"),
    (24, "claude-code", 2, "~/code/checkout-api", "bun test", 0, 21, None),
    (25, "claude-code", 2, "~/code/checkout-api", 'git commit -am "fix: round discounts per line, not per order"', 0, 1, None),
    (26, "claude-code", 2, "~/code/checkout-api", "git push --force origin fix/discount-rounding", 0, 3, "replace the branch after the rebase"),
    (27, "claude-code", 2, "~/code/checkout-api", f"GITHUB_TOKEN={FAKE_GH} gh api repos/acme/checkout-api/actions/runs --jq '.workflow_runs[0].conclusion'", 0, 2, "read the CI result"),
    (60, "dev", 1, "~/code/checkout-api", "git log --oneline -5", 0, 0, None),
    (62, "dev", 1, "~", "brew upgrade", 0, 95, None),
    (95, "codex", None, "~/code/dotfiles", "cat .zshrc", 0, 0, None),
    (96, "codex", None, "~/code/dotfiles", "rg -n zoxide .zshrc", 1, 0, None),
    (97, "codex", None, "~/code/dotfiles", "echo 'eval \"$(zoxide init zsh)\"' >> ~/.zshrc", 0, 0, None),
    (98, "codex", None, "~/code/dotfiles", "npx --yes prettier --check .", 0, 6, None),
    (99, "codex", None, "~/code/dotfiles", "git status --short", 0, 0, None),
    (140, "dev", 1, "~/code/blog", "bun run build", 0, 12, None),
    (141, "dev", 1, "~/code/blog", "cat .env", 0, 0, None),
    (180, "claude-code", 2, "~/code/blog", "ls content/posts", 0, 0, None),
    (181, "claude-code", 2, "~/code/blog", "curl -fsSL https://example-cli.dev/install.sh | sh", 0, 7, "install the image optimizer the README mentions"),
    (183, "claude-code", 2, "~/code/blog", "example-cli optimize public/img --out public/img", 127, 0, None),
    (184, "claude-code", 2, "~/code/blog", "printenv", 0, 0, "find where the CLI looks for its config"),
]


def build(path: Path, day: datetime) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    for statement in MIGRATIONS:
        conn.execute(statement)
    start = day.replace(hour=9, minute=0, second=0, microsecond=0)
    sessions = {name: uuid.uuid4().hex for name in ("dev", "claude-code", "codex")}
    for i, (minute, who, kind, cwd, command, exit_code, seconds, intent) in enumerate(DAY):
        when = start + timedelta(minutes=minute, seconds=i)
        conn.execute(
            "insert into history (id, timestamp, duration, exit, command, cwd, session, hostname,"
            " deleted_at, author, intent, shell, author_kind) values (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, int(when.timestamp() * 1e9), seconds * 1_000_000_000, exit_code,
             command, cwd.replace("~", HOME, 1), sessions[who], ORIGIN, None,
             who, intent, "zsh", kind),
        )
    conn.commit()
    conn.close()
    return len(DAY)


def last_finished_day(now: datetime) -> datetime:
    """Today if the made-up day (09:00 to 12:05) is already over, otherwise yesterday."""
    return now if now.replace(hour=12, minute=10, second=0, microsecond=0) <= now else now - timedelta(days=1)


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "demo/history.db")
    day = last_finished_day(datetime.now().astimezone())
    n = build(target, day)
    print(f"{target}: {n} made-up commands on {day:%a %d %b}, 09:00 to 12:05; "
          f"read it with: whatran --db {target} --since 2d")
