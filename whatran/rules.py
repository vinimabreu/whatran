"""Which commands deserve a second look, decided by fixed rules, never by the model.

Each rule is a regular expression with a reason a person can read. The model
later explains the day in plain words, but whether a command is flagged, and
why, is settled here, so the same command gets the same answer every day.
These are heuristics on the command text: they see what was typed, not what a
script did once it ran, and a rule can miss a command written in an unusual way.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .shellview import runnable

HIGH, MEDIUM = "high", "medium"


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    pattern: re.Pattern[str]
    why: str
    unless: Callable[[re.Match[str], str], bool] | None = None
    gate: re.Pattern[str] | None = None
    """When set, ``pattern`` reads the whole command (quotes and heredocs included)
    and the rule fires only if ``gate`` matches the part that runs here."""


def _r(id: str, severity: str, pattern: str, why: str, unless=None, gate: str | None = None) -> Rule:
    return Rule(id, severity, re.compile(pattern, re.IGNORECASE), why, unless,
                re.compile(gate, re.IGNORECASE) if gate else None)


REGENERABLE = {
    "node_modules", "dist", "build", "out", ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache",
    "target", "coverage", ".coverage", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", ".cache", ".venv", "venv", "*.pyc", ".eggs", "htmlcov", ".gradle", "DerivedData",
}


def _only_regenerable(match: re.Match[str], text: str) -> bool:
    """True when every path the ``rm`` names is a cache or build output that rebuilds itself."""
    rest = re.split(r"[;&|\n]", text[match.end():], maxsplit=1)[0]
    targets = [t for t in rest.split() if not t.startswith("-")]
    names = [t.rstrip("/").rsplit("/", 1)[-1] for t in targets]
    return bool(names) and all(n in REGENERABLE for n in names)


_SECRET_PATHS = (
    r"~?/?\.ssh/(?!\S*\.pub\b)(?!known_hosts\b)(?!config\b)|\bid_(rsa|ed25519|ecdsa)\b(?!\.pub)|\.aws/credentials|\.netrc\b|\.npmrc\b|\.pypirc\b"
    r"|\.docker/config\.json|\.kube/config|\.config/gh/hosts\.yml|(^|[\s/'\"])\.env(?!\.(example|sample|template|dist)\b)(\.[\w-]+)?\b"
)
_RC_FILES = r"(\.zshrc|\.zshenv|\.zprofile|\.bashrc|\.bash_profile|\.profile|config\.fish)\b"
_SENSITIVE_FILE = (r"(\.env(?!\.(example|sample|template|dist)\b)(\.[\w-]+)?\b|\.pem\b|\.key\b|\.p12\b|\.kdbx\b"
                   r"|\bid_(rsa|ed25519|ecdsa)\b(?!\.pub)|credentials\b|\.sql(\.gz)?\b|\.dump\b|\.sqlite3?\b|\.db\b)")
_READERS = r"\b(cat|less|more|head|tail|bat|grep|rg|cp|scp|rsync|base64|xxd|open|code|vim?|nano)\b"

RULES: tuple[Rule, ...] = (
    _r("pipe-to-shell", HIGH,
       r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da|k|fi)?sh\b",
       "downloads a script and runs it in the same breath, before anyone reads it"),
    _r("recursive-delete", HIGH,
       r"\brm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*|-r\s+-f|-f\s+-r|--recursive\s+--force|--force\s+--recursive)\b",
       "deletes a directory tree without asking", unless=_only_regenerable),
    _r("force-push", HIGH,
       r"\bgit\b.*\bpush\b.*(\s--force(?!-with-lease)\b|\s-f\b|\s\+\w)",
       "rewrites a branch on the remote; other people's commits can disappear"),
    _r("secret-read", HIGH,
       _READERS + r".*(" + _SECRET_PATHS + r")",
       "reads a file that holds keys or tokens; whatever it printed is now in the agent's conversation"),
    _r("keychain", HIGH,
       r"\bsecurity\s+(find|dump)-(generic|internet)-password\b|\bsecurity\s+dump-keychain\b|Login(\\)? Data|Cookies\.binarycookies",
       "reads saved passwords or browser credentials"),
    _r("env-dump", MEDIUM,
       r"(^|[;&|]\s*)(printenv|env|export\s+-p|set)\s*($|[|>;&])",
       "prints every environment variable, tokens included, into the agent's context"),
    _r("hard-reset", MEDIUM,
       r"\bgit\b.*\b(reset\s+--hard\b|clean\s+-\w*f|checkout\s+--\s+\.(\s|$)|restore\s+--source\b)",
       "throws away uncommitted work"),
    _r("sudo", MEDIUM, r"(^|[;&|]\s*)sudo\b", "runs with administrator rights"),
    _r("remote-sudo", MEDIUM,
       r"\bssh\b[^\n;&|]*?\s(['\"])(?:(?!\1).)*\bsudo\b",
       "runs a command with administrator rights on another machine, over ssh",
       gate=r"(^|[;&|(\s])ssh\s"),
    _r("world-writable", MEDIUM, r"\bchmod\s+(-R\s+)?(0?777|a\+w|o\+w)\b",
       "lets any user on the machine change the file"),
    _r("new-code", MEDIUM,
       r"\b(npm\s+(i|install|add|ci)\b(?!.*--ignore-scripts)|pnpm\s+(i|install|add|dlx)\b|yarn\s+(add|install|dlx)\b"
       r"|bun\s+(i|install|add)\b|bunx\b|npx\b|pip3?\s+install\b|uv\s+(pip\s+install|add|tool\s+install)\b"
       r"|uvx\b|brew\s+install\b|go\s+install\b|cargo\s+install\b|gem\s+install\b)",
       "can download and run third-party code; install scripts run on arrival"),
    _r("persistence", MEDIUM,
       r"(>>?|\btee\b(\s+-a)?)\s*\S*" + _RC_FILES + r"|\bsed\s+-i\b.*" + _RC_FILES
       + r"|\bcrontab\b(?!\s+-l\b)|\blaunchctl\s+(load|bootstrap|enable)\b"
       + r"|(>|\bcp\b|\bmv\b|\bln\b).*Library/Launch(Agents|Daemons)/",
       "makes something run again later on its own"),
    _r("secret-out", HIGH,
       r"\b(curl|wget|http|xh)\b.*(-F|--form|-T|--upload-file|--data(-binary|-raw)?|-d)\s*\S*@?\S*" + _SENSITIVE_FILE
       + r"|\b(scp|rsync|sftp)\b.*" + _SENSITIVE_FILE + r".*\s\S+:\S*",
       "sends a file that holds keys, tokens or data off this machine"),
    _r("db-destructive", MEDIUM,
       r"\b(drop\s+(table|database|schema)|truncate\s+table)\b|\bdelete\s+from\s+\w+\s*(;|$|['\"])",
       "deletes database data with no WHERE to limit it",
       gate=r"(^|[;&|(\s])(psql|mysql|mariadb|sqlite3|duckdb|mongosh|clickhouse-client|cockroach|supabase\s+db)\b"),
)


@dataclass(frozen=True)
class Flag:
    rule: Rule
    command: str


def check(command: str) -> list[Rule]:
    """The rules a command trips, most severe first.

    Rules look at :func:`whatran.shellview.runnable`, the part of the command the
    shell runs on this machine, so a heredoc of test data or a script sent over
    ``ssh`` does not count as something that ran here.
    """
    return [rule for rule, _, _ in _matches(command)]


def _matches(command: str) -> list[tuple[Rule, re.Match[str], str]]:
    text = runnable(command)
    found = []
    for rule in RULES:
        source = command if rule.gate is not None else text
        if rule.gate is not None and not rule.gate.search(text):
            continue
        match = rule.pattern.search(source)
        if match and not (rule.unless and rule.unless(match, text)):
            found.append((rule, match, source))
    return sorted(found, key=lambda t: (t[0].severity != HIGH, RULES.index(t[0])))


def evidence(command: str, width: int = 160) -> str:
    """The stretch of the command around what the most severe rule matched, on one line."""
    found = _matches(command)
    if not found:
        return command.strip().splitlines()[0][:width] if command.strip() else ""
    _, match, source = found[0]
    start = source.rfind("\n", 0, match.start()) + 1
    end = source.find("\n", match.start())
    line = source[start:] if end == -1 else source[start:end]
    pos = match.start() - start
    left = max(0, pos - 40)
    piece = line[left:left + width].strip()
    more = left + width < len(line) or "\n" in source.strip()
    return ("… " if left > 0 else "") + piece + (" …" if more else "")


_FULL = "[redacted]"
_SECRETS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), _FULL),
    (re.compile(r"\bsk-(ant-|proj-)?[A-Za-z0-9_\-]{16,}"), _FULL),
    (re.compile(r"\b(AKIA|ASIA)[A-Z0-9]{16}\b"), _FULL),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), _FULL),
    (re.compile(r"\b(glpat-[A-Za-z0-9_\-]{20,}|npm_[A-Za-z0-9]{30,})\b"), _FULL),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{16,}"), r"\1" + _FULL),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_?KEY)[A-Z0-9_]*\s*=\s*)(['\"]?)[^\s'\"]{4,}\2"),
     r"\1\2" + _FULL + r"\2"),
    (re.compile(r"(://[^:/\s]+:)[^@/\s]{3,}(@)"), r"\1" + _FULL + r"\2"),
)


def redact(command: str) -> str:
    """The command with anything that looks like a credential replaced.

    Applied before a command reaches the model or a report, so a summary can
    never repeat a token, even though nothing here leaves the machine.
    """
    for pattern, replacement in _SECRETS:
        command = pattern.sub(replacement, command)
    return command
