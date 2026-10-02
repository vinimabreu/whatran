"""Read the commands Claude Code ran from the session files it already keeps.

Claude Code writes every session to ``~/.claude/projects/<project>/<session>.jsonl``.
Each shell command it runs is a ``Bash`` tool call with the command and a short
``description`` of why, followed by a result. That is enough for whatran with
nothing to install: the description becomes the intent, and the result tells
whether the command ran, failed, or was stopped before it ran.

Only the command, its description, its folder, its time and how it ended are
read. The output of the command, and the rest of the conversation, are not.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

from .history import AGENT, Entry

AUTHOR = "claude-code"
_EXIT = re.compile(r"Exit code (-?\d+)")
_NOT_RUN = re.compile(
    r"Permission for this action was denied|doesn't want to proceed|tool use was rejected"
    r"|<tool_use_error>|Blocked:|hook (blocked|denied)|was blocked by",
    re.IGNORECASE,
)


def default_root() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR")
    return (Path(base).expanduser() if base else Path.home() / ".claude") / "projects"


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.get("text", "") for b in content if isinstance(b, dict))
    return ""


def outcome(result: dict) -> tuple[bool, int]:
    """``(ran, exit)`` from a tool result; exit is -1 when it is not known."""
    text = _text(result.get("content"))
    if not result.get("is_error"):
        return True, 0 if "running in background" not in text else -1
    if _NOT_RUN.search(text[:400]):
        return False, -1
    match = _EXIT.search(text[:200])
    return True, int(match.group(1)) if match else 1


def _when(stamp: str) -> datetime | None:
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def read_file(path: Path) -> list[Entry]:
    calls: dict[str, dict] = {}
    results: dict[str, dict] = {}
    started_in: str | None = None
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if started_in is None and isinstance(row.get("cwd"), str) and row["cwd"]:
                started_in = row["cwd"]
            content = (row.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if (row.get("type") == "assistant" and block.get("type") == "tool_use"
                        and block.get("name") == "Bash" and isinstance(block.get("input"), dict)):
                    calls[block.get("id", "")] = {
                        "command": block["input"].get("command", ""),
                        "intent": block["input"].get("description"),
                        "when": _when(row.get("timestamp", "")),
                        "cwd": row.get("cwd", ""),
                        "session": row.get("sessionId", path.stem),
                    }
                elif row.get("type") == "user" and block.get("type") == "tool_result":
                    results[block.get("tool_use_id", "")] = block
    entries = []
    for call_id, call in calls.items():
        if not call["command"] or call["when"] is None:
            continue
        ran, exit_code = outcome(results[call_id]) if call_id in results else (True, -1)
        entries.append(Entry(
            id=call_id, when=call["when"], duration_ms=0, exit=exit_code, command=call["command"],
            cwd=call["cwd"], session=call["session"], origin=AUTHOR, author=AUTHOR,
            intent=call["intent"] or None, author_kind=AGENT, ran=ran, project=started_in,
        ))
    return entries


def load(root: Path, since: datetime, until: datetime | None = None) -> list[Entry]:
    """Every Bash command Claude Code ran in ``[since, until)``, oldest first."""
    if not root.is_dir():
        return []
    cutoff = since.timestamp()
    out = []
    for path in root.rglob("*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff:
                continue
        except OSError:
            continue
        out.extend(e for e in read_file(path)
                   if e.when >= since and (until is None or e.when < until))
    return sorted(out, key=lambda e: (e.when, e.id))
