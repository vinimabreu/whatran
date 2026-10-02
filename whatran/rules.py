"""Which commands deserve a second look, decided by fixed rules, never by the model.

Each rule is a regular expression with a reason a person can read. The model
later explains the day in plain words, but whether a command is flagged, and
why, is settled here, so the same command gets the same answer every day.
These are heuristics on the command text: they see what was typed, not what a
script did once it ran, and a rule can miss a command written in an unusual way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HIGH, MEDIUM = "high", "medium"


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    pattern: re.Pattern[str]
    why: str


def _r(id: str, severity: str, pattern: str, why: str) -> Rule:
    return Rule(id, severity, re.compile(pattern, re.IGNORECASE), why)


_SECRET_PATHS = (
    r"~?/?\.ssh/(?!\S*\.pub\b)(?!known_hosts\b)(?!config\b)|\bid_(rsa|ed25519|ecdsa)\b(?!\.pub)|\.aws/credentials|\.netrc\b|\.npmrc\b|\.pypirc\b"
    r"|\.docker/config\.json|\.kube/config|\.config/gh/hosts\.yml|(^|[\s/'\"])\.env(?!\.(example|sample|template|dist)\b)(\.[\w-]+)?\b"
)
_RC_FILES = r"(\.zshrc|\.zshenv|\.zprofile|\.bashrc|\.bash_profile|\.profile|config\.fish)\b"
_READERS = r"\b(cat|less|more|head|tail|bat|grep|rg|cp|scp|rsync|base64|xxd|open|code|vim?|nano)\b"

RULES: tuple[Rule, ...] = (
    _r("pipe-to-shell", HIGH,
       r"\b(curl|wget)\b[^|;&]*\|\s*(sudo\s+)?(ba|z|da|k|fi)?sh\b",
       "downloads a script and runs it in the same breath, before anyone reads it"),
    _r("recursive-delete", HIGH,
       r"\brm\s+(-\w*r\w*f\w*|-\w*f\w*r\w*|-r\s+-f|-f\s+-r|--recursive\s+--force|--force\s+--recursive)\b",
       "deletes a directory tree without asking"),
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
    _r("world-writable", MEDIUM, r"\bchmod\s+(-R\s+)?(0?777|a\+w|o\+w)\b",
       "lets any user on the machine change the file"),
    _r("new-code", MEDIUM,
       r"\b(npm\s+(i|install|add|ci)\b(?!.*--ignore-scripts)|pnpm\s+(i|install|add|dlx)\b|yarn\s+(add|install|dlx)\b"
       r"|bun\s+(i|install|add)\b|bunx\b|npx\b|pip3?\s+install\b|uv\s+(pip\s+install|add|tool\s+install)\b"
       r"|uvx\b|brew\s+install\b|go\s+install\b|cargo\s+install\b|gem\s+install\b)",
       "brings third-party code onto the machine, and install scripts run on arrival"),
    _r("persistence", MEDIUM,
       r"(>>?|\btee\b(\s+-a)?)\s*\S*" + _RC_FILES + r"|\bsed\s+-i\b.*" + _RC_FILES
       + r"|\bcrontab\b(?!\s+-l\b)|\blaunchctl\s+(load|bootstrap|enable)\b"
       + r"|(>|\bcp\b|\bmv\b|\bln\b).*Library/Launch(Agents|Daemons)/",
       "makes something run again later on its own"),
    _r("data-out", MEDIUM,
       r"\bcurl\b.*\s(-d|--data(-\w+)?|-F|--form|-T|--upload-file)\b(?!.*\b(localhost|127\.0\.0\.1)\b)"
       r"|\b(scp|rsync)\b\s.*\s[\w.-]+@?[\w.-]+:\S*|\bnc\s+\S+\s+\d+",
       "sends data from this machine to another one"),
    _r("db-destructive", MEDIUM,
       r"\b(drop\s+(table|database|schema)|truncate\s+table)\b|\bdelete\s+from\s+\w+\s*(;|$|['\"])",
       "deletes database data with no WHERE to limit it"),
)


@dataclass(frozen=True)
class Flag:
    rule: Rule
    command: str


def check(command: str) -> list[Rule]:
    """The rules a command trips, most severe first."""
    hits = [rule for rule in RULES if rule.pattern.search(command)]
    return sorted(hits, key=lambda r: (r.severity != HIGH, RULES.index(r)))


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
