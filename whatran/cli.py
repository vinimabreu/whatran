"""Command line: ``whatran`` for today, ``whatran --since 24h`` for a window."""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

from . import __version__, claude_code, digest, history, model

_SPAN = re.compile(r"^(\d+)\s*([hdw])$")


def parse_since(text: str, now: datetime) -> datetime:
    """``today``, ``24h``, ``3d``, ``1w`` or a date like ``2026-10-01``, in local time."""
    text = text.strip()
    if text.lower() == "today":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    match = _SPAN.match(text.lower())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        return now - {"h": timedelta(hours=amount), "d": timedelta(days=amount),
                      "w": timedelta(weeks=amount)}[unit]
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00")).astimezone()
    except ValueError:
        raise argparse.ArgumentTypeError(f"cannot read {text!r}; use today, 24h, 3d, 1w or 2026-10-01") from None


def main(argv: list[str] | None = None, *, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="whatran",
        description="What your coding agents ran in your terminal, from your atuin history, "
                    "explained by a model running on this machine.")
    parser.add_argument("--since", default="today", help="today (default), 24h, 3d, 1w or a date")
    parser.add_argument("--db", type=Path, default=None, help="atuin database (default: atuin's own)")
    parser.add_argument("--source", choices=("auto", "atuin", "claude-code"), default="auto",
                        help="auto (default): atuin, plus Claude Code's session files when atuin "
                             "has no Claude Code commands; with --db, only that database")
    parser.add_argument("--claude-dir", type=Path, default=None,
                        help="Claude Code projects folder (default ~/.claude/projects)")
    parser.add_argument("--all", action="store_true", help="flag your own commands too, not only the agents'")
    parser.add_argument("--model", default=model.DEFAULT_MODEL, help="Ollama model (default %(default)s)")
    parser.add_argument("--no-model", action="store_true", help="facts and flags only, no note")
    parser.add_argument("--lang", default="en", help="language of the note: en or pt (default en)")
    parser.add_argument("--json", action="store_true", help="print the facts and the note as JSON")
    parser.add_argument("--allow-remote-model", action="store_true",
                        help="let OLLAMA_HOST point at another machine (off by default)")
    parser.add_argument("--version", action="version", version=f"whatran {__version__}")
    args = parser.parse_args(argv)

    now = (now or datetime.now()).astimezone()
    try:
        since = parse_since(args.since, now)
    except argparse.ArgumentTypeError as error:
        parser.error(str(error))
    path = args.db or history.default_db_path()
    claude_root = args.claude_dir or claude_code.default_root()
    entries: list[history.Entry] = []
    have_atuin = False
    if args.source in ("auto", "atuin"):
        try:
            with history.connect(path) as conn:
                entries = history.load(conn, since, now)
            have_atuin = True
        except (FileNotFoundError, ValueError, sqlite3.DatabaseError) as error:
            if args.source == "atuin" or args.db is not None:
                reason = error if not isinstance(error, sqlite3.DatabaseError) else f"{path} is not an atuin database"
                print(f"whatran: {reason}", file=sys.stderr)
                return 2
    # an explicit --db is read alone, so the demo never mixes in your real sessions
    use_claude = args.source == "claude-code" or (
        args.source == "auto" and args.db is None and not any(e.author == claude_code.AUTHOR for e in entries))
    if use_claude:
        if not claude_root.is_dir():
            if args.source == "claude-code" or not have_atuin:
                where = "an atuin history or " if args.source == "auto" else ""
                print(f"whatran: found no {where}Claude Code sessions at {claude_root}", file=sys.stderr)
                return 2
        else:
            entries = sorted(entries + claude_code.load(claude_root, since, now), key=lambda e: (e.when, e.id))

    facts = digest.build(entries, since, now, include_yours=args.all, yours_known=have_atuin)
    note, problems, model_error = None, [], None
    if not args.no_model and facts.total - (facts.yours or 0) > 0:
        system, user = digest.prompt(facts, lang=args.lang)
        try:
            draft = model.chat(system, user, model=args.model, allow_remote=args.allow_remote_model)
            problems = digest.check_note(draft, facts)
            if problems:
                feedback = user + "\n\nYour previous note had these problems; fix them:\n" + "\n".join(problems)
                draft = model.chat(system, feedback, model=args.model, allow_remote=args.allow_remote_model)
                problems = digest.check_note(draft, facts)
            note = None if problems else draft
        except (model.RemoteHostRefused, model.ModelUnavailable) as error:
            model_error = str(error)

    if args.json:
        print(json.dumps({"facts": facts.as_dict(), "note": note, "note_problems": problems,
                          "model": None if args.no_model else args.model, "model_error": model_error},
                         ensure_ascii=False, indent=2))
        return 0
    print(digest.render(facts, note, model=args.model, note_problems=problems, now=now))
    if model_error:
        print(f"\n(no note: {model_error})")
    return 0
