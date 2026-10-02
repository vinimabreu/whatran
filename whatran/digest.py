"""Turn a window of history into facts, and the facts into a note a person reads.

The split is the whole design. Counting, grouping and flagging are plain code
over the database, so every number and every flag is reproducible. The model
gets those facts and writes the paragraph, and its paragraph is checked before
it is shown: every command it quotes must be one the facts contain, and every
number it uses must be one the facts state. A paragraph that fails the check is
not shown; the facts are, because they never depended on the model.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import rules
from .history import Entry

MAX_SAMPLES = 8
MAX_EXAMPLES = 3
CONTEXT = 2
"""Commands shown to the model on each side of a flagged one, from the same session."""


_ROOTS: dict[str, str] = {}


def project_of(path: str, session_root: str | None = None) -> str:
    """Where a command belongs: its git repository, else the folder its session started in
    when the command ran inside it, else the folder itself."""
    if path not in _ROOTS:
        root = ""
        p = Path(path)
        for candidate in (p, *p.parents):
            try:
                if (candidate / ".git").exists():
                    root = str(candidate)
                    break
            except OSError:
                break
        _ROOTS[path] = root
    if _ROOTS[path]:
        return _ROOTS[path]
    if session_root and (path == session_root or path.startswith(session_root.rstrip("/") + "/")):
        return session_root
    return path


def short_dir(path: str, home: str | None = None) -> str:
    home = home if home is not None else str(Path.home())
    if home and (path == home or path.startswith(home.rstrip("/") + "/")):
        return "~" + path[len(home.rstrip("/")):]
    return re.sub(r"^/(Users|home)/[^/]+", "~", path)


@dataclass
class Facts:
    start: datetime
    end: datetime
    total: int
    yours: int | None
    blocked: int = 0
    agents: dict[str, dict] = field(default_factory=dict)
    flags: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "window": {"from": self.start.isoformat(timespec="minutes"),
                       "to": self.end.isoformat(timespec="minutes")},
            "commands": self.total,
            "yours": self.yours,
            "by_agents": self.total - (self.yours or 0),
            "blocked_before_running": self.blocked,
            "agents": self.agents,
            "flags": self.flags,
        }


def build(entries: list[Entry], start: datetime, end: datetime, *,
          include_yours: bool = False, home: str | None = None, yours_known: bool = True) -> Facts:
    """Everything the report says, computed without a model.

    Commands that were stopped before they ran are counted apart and never as
    failures; ``yours_known`` is False when the source only holds agent commands.
    """
    ran = [e for e in entries if e.ran]
    yours = sum(1 for e in ran if not e.is_agent) if yours_known else None
    facts = Facts(start=start, end=end, total=len(ran), yours=yours,
                  blocked=sum(1 for e in entries if not e.ran))
    started: dict[str, str] = {}
    for e in entries:
        started.setdefault(e.session, e.project or e.cwd)

    def where(e: Entry) -> str:
        return short_dir(project_of(e.cwd, started.get(e.session)), home)

    per_agent: dict[str, list[Entry]] = defaultdict(list)
    for e in ran:
        if e.is_agent:
            per_agent[e.who].append(e)
    for name in sorted(per_agent, key=lambda n: (-len(per_agent[n]), n)):
        runs = per_agent[name]
        projects: dict[str, list[Entry]] = defaultdict(list)
        for e in runs:
            projects[where(e)].append(e)
        facts.agents[name] = {
            "commands": len(runs),
            "failed": sum(1 for e in runs if e.exit not in (0, -1)),
            "projects": {
                d: {
                    "commands": len(group),
                    "failed": sum(1 for e in group if e.exit not in (0, -1)),
                    "intents": sorted({rules.redact(e.intent) for e in group if e.intent}),
                    "most_run": [cmd for cmd, _ in Counter(
                        clip(rules.redact(e.command)) for e in group).most_common(MAX_SAMPLES)],
                }
                for d, group in sorted(projects.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            },
        }
    sessions: dict[str, list[Entry]] = defaultdict(list)
    for e in entries:
        sessions[e.session].append(e)
    groups: dict[tuple, list[tuple[Entry, list]]] = {}
    for e in entries:
        if not e.is_agent and not include_yours:
            continue
        hits = rules.check(e.command)
        if hits:
            key = (e.who, where(e), tuple(r.id for r in hits), e.ran)
            groups.setdefault(key, []).append((e, hits))
    for (who, where, _, did_run), items in groups.items():
        first, hits = items[0]
        around = sessions[first.session]
        i = around.index(first)
        examples = list(dict.fromkeys(rules.evidence(rules.redact(e.command)) for e, _ in items))
        facts.flags.append({
            "id": f"F{len(facts.flags) + 1}",
            "when": first.when.astimezone().strftime("%H:%M"),
            "last": items[-1][0].when.astimezone().strftime("%H:%M"),
            "times": len(items),
            "who": who,
            "dir": where,
            "command": examples[0],
            "other_examples": examples[1:1 + MAX_EXAMPLES - 1],
            "exit": first.exit,
            "failed": sum(1 for e, _ in items if e.ran and e.exit not in (0, -1)),
            "ran": did_run,
            "intent": rules.redact(first.intent) if first.intent else None,
            "intents": list(dict.fromkeys(rules.redact(e.intent) for e, _ in items if e.intent))[:MAX_EXAMPLES],
            "rules": [{"id": r.id, "severity": r.severity, "why": r.why} for r in hits],
            "before": [_step(x) for x in around[max(0, i - CONTEXT):i]],
            "after": [_step(x) for x in around[i + 1:i + 1 + CONTEXT]],
        })
    return facts


def clip(command: str, width: int = 160) -> str:
    """One line of a command, at most ``width`` characters, marked when cut."""
    lines = command.strip().splitlines() or [""]
    head = lines[0]
    cut = len(head) > width
    head = head[:width].rstrip()
    if cut or len(lines) > 1:
        extra = f" (+{len(lines) - 1} lines)" if len(lines) > 1 else ""
        return f"{head} …{extra}"
    return head


def _step(e: Entry) -> dict:
    step = {"command": clip(rules.redact(e.command)), "exit": e.exit,
            "intent": rules.redact(e.intent) if e.intent else None}
    if not e.ran:
        step["ran"] = False
    return step


SYSTEM = """You write a short end-of-day note to a developer about what the AI coding agents ran in his terminal.
You get the facts as JSON. Use only those facts.
- Never invent a command, file, project, agent or number. When you mention a command, copy it exactly, inside backticks, from the facts.
- Open with one or two sentences on what each agent was working on in each project, from its intents and commands.
- Then take every flag, in order, by its id in square brackets like [F1]. A flag with "times" above 1 groups several different commands that tripped the same rule in one project; "command" is one example of them, not a command repeated that many times. Talk about the group once. In one or two sentences say what the agent was trying to do when it ran it, using the flag's intent and the commands before and after it, and what the developer should check now. If the commands after it show it failed or led to trouble, say so. If a flag has "ran": false, it was stopped before running: say it never ran.
- The reader already sees the totals and each flag's reason above your note: do not restate them.
- Plain sentences, no headings, no bullet lists. At most {words} words.
- Write in {language}."""

_LANG = {"en": "English", "pt": "Brazilian Portuguese"}


def prompt(facts: Facts, *, lang: str = "en", words: int = 220) -> tuple[str, str]:
    system = SYSTEM.format(words=words, language=_LANG.get(lang, lang))
    return system, json.dumps(facts.as_dict(), ensure_ascii=False, indent=1)


_CODE = re.compile(r"`([^`\n]+)`")
_TIME = re.compile(r"(?<![\d:])(\d{1,2}):(\d{2})(?![\d:])")
_NUMBER = re.compile(r"(?<![\w.\[/-])(\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(%)?(?![\w/:-])")


def _counts(facts: Facts) -> set[int]:
    """The numbers the facts state as counts: the only numbers a note may use."""
    out = {facts.total, facts.total - (facts.yours or 0), facts.blocked, len(facts.flags), len(facts.agents),
           sum(len(a["projects"]) for a in facts.agents.values())}
    if facts.yours is not None:
        out.add(facts.yours)
    for agent in facts.agents.values():
        out.update((agent["commands"], agent["failed"], len(agent["projects"])))
        for project in agent["projects"].values():
            out.update((project["commands"], project["failed"]))
    for f in facts.flags:
        out.update((f["times"], f["failed"], f["exit"]))
        out.update(step["exit"] for step in f["before"] + f["after"])
    return out


def check_note(note: str, facts: Facts) -> list[str]:
    """What in the model's note is not backed by the facts; empty means it passed.

    - every command quoted in backticks must appear in the facts;
    - every number must be one of the counts or exit codes the facts state, and
      every clock time one of the flags' times (a year, a percentage or a time
      read as a number does not count);
    - every flag id it cites must exist, and every flag must be cited.
    Numbers written as words ("two") are not checked.
    """
    texts = [f["command"] for f in facts.flags]
    for f in facts.flags:
        texts.extend(f["other_examples"])
        texts.extend(step["command"] for step in f["before"] + f["after"])
        texts.extend(r["id"] for r in f["rules"])
    for agent in facts.agents.values():
        for name, project in agent["projects"].items():
            texts.append(name)
            texts.extend(project["most_run"])
    texts.extend(facts.agents)
    problems = []
    for quoted in _CODE.findall(note):
        q = quoted.strip()
        if not any(q in t for t in texts):
            problems.append(f"quotes `{q}`, which no fact contains")
    outside = _CODE.sub(" ", note)
    times = {t for f in facts.flags for t in (f["when"], f["last"])}
    for hh, mm in _TIME.findall(outside):
        if f"{int(hh):02d}:{mm}" not in times:
            problems.append(f"gives the time {hh}:{mm}, which is not when any flag happened")
    outside = _TIME.sub(" ", outside)
    counts = _counts(facts)
    for digits, percent in _NUMBER.findall(outside):
        value = int(digits.replace(",", ""))
        if percent or value not in counts:
            problems.append(f"uses the number {digits}{percent}, which no fact states")
    cited = set()
    for group in re.findall(r"\[(F\d+(?:\s*(?:,|-|\u2013|to)\s*F?\d+)*)\]", note):
        # [F1], [F1, F2], [F1-F3]
        for a, b in re.findall(r"F?(\d+)(?:\s*(?:-|\u2013|to)\s*F?(\d+))?", group):
            cited.update(f"F{n}" for n in range(int(a), int(b or a) + 1))
    for a, b in re.findall(r"\[F(\d+)\]\s*(?:-|\u2013|to)\s*\[F(\d+)\]", note):
        cited.update(f"F{n}" for n in range(int(a), int(b) + 1))
    known = [f["id"] for f in facts.flags]
    problems.extend(f"cites [{i}], which is not a flag" for i in sorted(cited - set(known)))
    problems.extend(f"skips [{i}]" for i in known if i not in cited)
    return problems


def render(facts: Facts, note: str | None, *, model: str | None = None,
           note_problems: list[str] | None = None, now: datetime | None = None) -> str:
    start = facts.start.astimezone()
    end = (now or facts.end).astimezone()
    if start.date() == end.date():
        lines = [f"whatran · {start:%a %d %b}, {start:%H:%M}-{end:%H:%M}"]
    else:
        lines = [f"whatran · {start:%a %d %b %H:%M} to {end:%a %d %b %H:%M}"]
    by = ", ".join(f"{name} {a['commands']}" for name, a in facts.agents.items())
    failed = sum(a["failed"] for a in facts.agents.values())
    agent_total = facts.total - (facts.yours or 0)
    if facts.yours is None:
        summary = f"{agent_total} command{'s' if agent_total != 1 else ''} by agents"
    else:
        summary = (f"{facts.total} command{'s' if facts.total != 1 else ''}: "
                   f"{facts.yours} yours, {agent_total} by agents")
    lines.append(summary + (f" ({by})." if by else "."))
    if agent_total:
        lines.append(f"{failed} agent command{'s' if failed != 1 else ''} failed.")
    if facts.blocked:
        lines.append(f"{facts.blocked} stopped before running (denied or blocked).")
    if facts.agents:
        lines.append("")
        lines.append("Where the agents worked")
        for name, agent in facts.agents.items():
            for d, p in agent["projects"].items():
                fail = f", {p['failed']} failed" if p["failed"] else ""
                lines.append(f"  {name:<12} {d}  {p['commands']} commands{fail}")
    lines.append("")
    if facts.flags:
        lines.append(f"Worth a look ({len(facts.flags)})")
        for f in facts.flags:
            span = f["when"] if f["times"] == 1 else f"{f['when']}-{f['last']}"
            if not f["ran"]:
                note_ = "  (stopped, never ran)"
            elif f["times"] > 1:
                note_ = f"  x{f['times']}" + (f", {f['failed']} failed" if f["failed"] else "")
            else:
                note_ = "" if f["exit"] in (0, -1) else f"  (exit {f['exit']})"
            lines.append(f"  [{f['id']}] {span} {f['who']} in {f['dir']}{note_}")
            lines.append(f"       {f['command']}")
            for r in f["rules"]:
                lines.append(f"       {r['severity']} · {r['why']}")
    else:
        lines.append("Nothing flagged.")
    if note is not None:
        lines.append("")
        lines.append(f"In plain words ({model}, running on this machine)")
        lines.append(note)
    elif note_problems:
        lines.append("")
        lines.append(f"The note from {model} was not shown, because it did not match the facts:")
        lines.extend(f"  - {p}" for p in note_problems)
    return "\n".join(lines)
