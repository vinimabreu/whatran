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

from .shellview import commands, heredocs, is_ssh, join_lines, pipe_parts, runnable, split, ssh_scripts, strip_sudo

HIGH, MEDIUM = "high", "medium"


@dataclass(frozen=True)
class Rule:
    id: str
    severity: str
    pattern: re.Pattern[str]
    why: str
    unless: Callable[[re.Match[str], str], bool] | None = None
    scope: str = "command"
    """``command``: matched against each simple command, wrappers like ``sudo`` taken off.
    ``line``: matched against the whole runnable text (pipelines, ``sh -c`` scripts).
    ``raw``: matched against the command as typed, quotes included, but only when
    ``gate`` matches one of its simple commands (a database client, for example)."""
    gate: re.Pattern[str] | None = None


def _r(id: str, severity: str, pattern: str, why: str, unless=None, scope: str = "command",
       gate: str | None = None) -> Rule:
    return Rule(id, severity, re.compile(pattern, re.IGNORECASE), why, unless, scope,
                re.compile(gate, re.IGNORECASE) if gate else None)


REGENERABLE = {
    "node_modules", "dist", "build", "out", ".next", ".nuxt", ".svelte-kit", ".turbo", ".parcel-cache",
    "target", "coverage", ".coverage", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", ".cache", ".venv", "venv", "*.pyc", ".eggs", "htmlcov", ".gradle", "DerivedData",
}
_REDIRECT = re.compile(r"^(\d*|&)(>>?|<)")


def _only_regenerable(match: re.Match[str], command: str) -> bool:
    """True when every path the ``rm`` names is a cache or build output that rebuilds itself.

    For ``find ... -exec rm -rf {}`` that means every ``-name`` is one; ``xargs rm -rf``
    names nothing, so it never counts as a cleanup.
    """
    if command.startswith("find "):
        names = [n.strip("'\"") for n in re.findall(r"-i?name\s+(\S+)", command)]
        return bool(names) and all(n in REGENERABLE for n in names)
    if command.startswith("xargs"):
        return False
    words = command[match.end():].split()
    targets, skip = [], False
    for w in words:
        if skip:
            skip = False
        elif _REDIRECT.match(w):
            skip = w in {">", ">>", "<", "2>", "2>>", "&>"}
        elif not w.startswith("-"):
            targets.append(w)
    names = [t.rstrip("/").rsplit("/", 1)[-1] for t in targets]
    return bool(names) and all(n in REGENERABLE for n in names)


_SECRET_PATHS = (
    r"~?/?\.ssh/(?!\S*\.pub\b)(?!known_hosts\b)(?!config\b)|\bid_(rsa|ed25519|ecdsa)\b(?!\.pub)"
    r"|\.aws/credentials|\.netrc\b|\.npmrc\b|\.pypirc\b|\.git-credentials\b"
    r"|\.docker/config\.json|\.kube/config|\.config/gh/hosts\.yml"
    r"|(^|[\s/'\"=])\.env(?!\.(example|sample|template|dist)\b)(\.[\w-]+)?\b"
)
_RC_FILES = r"(\.zshrc|\.zshenv|\.zprofile|\.bashrc|\.bash_profile|\.profile|config\.fish)\b"
_SENSITIVE_FILE = (r"(\.env(?!\.(example|sample|template|dist)\b)(\.[\w-]+)?\b|\.pem\b|\.key\b|\.p12\b|\.kdbx\b"
                   r"|\bid_(rsa|ed25519|ecdsa)\b(?!\.pub)|credentials\b|\.sql(\.gz)?\b|\.dump\b|\.sqlite3?\b|\.db\b)")
_READERS = r"(cat|less|more|head|tail|bat|grep|rg|ag|awk|sed|jq|yq|strings|cp|scp|rsync|base64|xxd|od|open|code|vim?|nvim|nano)"


def _copies_onto_secret(match: re.Match[str], command: str) -> bool:
    """``cp .env.example .env`` writes a secret file, it does not read one."""
    words = command.split()
    if not words or words[0] not in {"cp", "mv", "install", "rsync", "scp"}:
        return False
    secret = re.compile(_SECRET_PATHS, re.IGNORECASE)
    args = [w for w in words[1:] if not w.startswith("-")]
    return len(args) >= 2 and not any(secret.search(a) for a in args[:-1])


RULES: tuple[Rule, ...] = (
    _r("pipe-to-shell", HIGH,
       r"\b(curl|wget)\b[^|;&\n]*\|\s*(sudo\s+(-\S+\s+)*)?(env\s+)?(\S*/)?"
       r"((ba|z|da|k|fi)?sh\b(?!\s+-[a-z]*c\b)|(python3?|perl|ruby|node)\s*(-\s*)?(?=$|[;&|)\n]))"
       r"|\b(ba|z|da|k)?sh\s+(-\S+\s+)*<\(\s*(curl|wget)\b"
       r"|\b((ba|z|da|k)?sh\s+-[a-z]*c|eval)\s*;\s*\$\(\s*(curl|wget)\b",
       "downloads a script and runs it in the same breath, before anyone reads it", scope="line"),
    _r("recursive-delete", HIGH,
       r"^(find\b.*\s-exec(dir)?\s+|xargs\s+(-\S+\s+)*)?(\S*/)?rm\s+"
       r"(-\w*r\w*f\w*|-\w*f\w*r\w*|-r\s+-f|-f\s+-r|--recursive\s+--force|--force\s+--recursive)\b",
       "deletes a directory tree without asking", unless=_only_regenerable),
    _r("force-push", HIGH,
       r"^git\b.*\bpush\b.*(\s--force(?!-with-lease)\b|\s-f\b|\s\+[\w/.-]+)",
       "rewrites a branch on the remote; other people's commits can disappear"),
    _r("secret-read", HIGH,
       r"^" + _READERS + r"\b.*(" + _SECRET_PATHS + r")"
       r"|^gh\s+auth\s+token\b|^printenv\s+\w*(TOKEN|SECRET|KEY|PASSWORD|PASSWD)\w*",
       "reads a file that holds keys or tokens; whatever it printed is now in the agent's conversation",
       unless=_copies_onto_secret),
    _r("keychain", HIGH,
       r"\bsecurity\s+(find|dump)-(generic|internet)-password\b|\bsecurity\s+dump-keychain\b|Login(\\)? Data|Cookies\.binarycookies",
       "reads saved passwords or browser credentials"),
    _r("env-dump", MEDIUM,
       r"^(sudo\s+)?(printenv|env|export\s+-p|set|declare\s+-p)\s*(\d?>.*)?$",
       "prints every environment variable, tokens included, into the agent's context", scope="as-typed"),
    _r("hard-reset", MEDIUM,
       r"^git\b.*\b(reset\s+--hard\b|clean\s+-\w*f|checkout\s+--\s+\.(\s|$)|restore\s+--source\b)",
       "throws away uncommitted work"),
    _r("sudo", MEDIUM, r"^(sudo|doas)(\s|$)", "runs with administrator rights", scope="as-typed"),
    _r("world-writable", MEDIUM, r"^chmod\b.*\s(0?777|a\+w|o\+w)\b",
       "lets any user on the machine change the file"),
    _r("new-code", MEDIUM,
       r"^(npm\s+(i|install|add|ci)\b(?!.*--ignore-scripts)|pnpm\s+(i|install|add|dlx)\b|yarn\s+(add|install|dlx)\b"
       r"|bun\s+(i|install|add)\b|bunx\b|npx\b|pip3?\s+install\b|python3?\s+-m\s+pip\s+install\b"
       r"|uv\s+(pip\s+install|add|tool\s+install)\b|uvx\b|brew\s+install\b|go\s+install\b|cargo\s+install\b|gem\s+install\b)",
       "can download and run third-party code; install scripts run on arrival"),
    _r("persistence", MEDIUM,
       r"(>>?|^tee\b(\s+-a)?)\s*\S*" + _RC_FILES + r"|^sed\s+-i\b.*" + _RC_FILES
       + r"|^crontab\b(?!\s+-l\b)|^launchctl\s+(load|bootstrap|enable)\b"
       + r"|^(cp|mv|ln)\b.*Library/Launch(Agents|Daemons)/|>\s*\S*Library/Launch(Agents|Daemons)/",
       "makes something run again later on its own"),
    _r("secret-out", HIGH,
       r"^(curl|wget|http|xh)\b.*(-F|--form|-T|--upload-file|--data(-binary|-raw)?|-d)\s*\S*@?\S*" + _SENSITIVE_FILE
       + r"|^(scp|rsync|sftp)\b.*" + _SENSITIVE_FILE + r".*\s\S+:\S*",
       "sends a file that holds keys, tokens or data off this machine"),
    _r("db-destructive", MEDIUM,
       r"\b(drop\s+(table|database|schema)|truncate\s+table)\b|\bdelete\s+from\s+\w+\s*(;|$|['\"])",
       "deletes database data with no WHERE to limit it", scope="raw",
       gate=r"^(psql|mysql|mariadb|sqlite3|duckdb|mongosh|clickhouse-client|cockroach|supabase)$"),
)


def check(command: str) -> list[Rule]:
    """The rules a command trips, most severe first.

    Rules read :func:`whatran.shellview.runnable`, the part of the command the
    shell runs, one simple command at a time, so a heredoc of test data or a
    commit message does not count. Scripts sent over ``ssh`` go through the same
    rules and come back as ``<rule>@remote``.
    """
    return [rule for rule, _, _ in _matches(command)]


def _local(command: str) -> list[tuple[Rule, re.Match[str], str]]:
    text = runnable(command)
    simple = [c for c in commands(text) if not is_ssh(c)]
    found = []
    for rule in RULES:
        hit = None
        if rule.scope == "line":
            m = rule.pattern.search(text)
            hit = (m, text) if m else None
        elif rule.scope == "raw":
            hit = _raw_hit(rule, command)
        else:
            for c in simple:
                view = c if rule.scope == "as-typed" else strip_sudo(c)
                m = rule.pattern.search(view)
                if m and not (rule.unless and rule.unless(m, view)):
                    hit = (m, view)
                    break
        if hit:
            found.append((rule, hit[0], hit[1]))
    return found


def _raw_hit(rule: Rule, command: str) -> tuple[re.Match[str], str] | None:
    """A ``raw`` rule reads, quotes included, only the simple commands that run one of its
    programs (``psql``, or ``docker exec db psql``), plus the heredocs fed to them."""
    def runs_gated(segment: str) -> bool:
        return any(rule.gate.search(w.strip("'\"").rsplit("/", 1)[-1]) for w in segment.split())

    text, docs = heredocs(join_lines(command))
    for pipeline in split(text):
        for part in pipe_parts(pipeline):
            if not is_ssh(part) and runs_gated(part.split("<<", 1)[0]):
                m = rule.pattern.search(part)
                if m:
                    return m, part
    for doc in docs:
        if doc.program != "ssh" and runs_gated(doc.head.split("<<", 1)[0]):
            m = rule.pattern.search(doc.body)
            if m:
                return m, doc.body
    return None


def remote(rule: Rule) -> Rule:
    """The same rule, for a command that ran on another machine through ``ssh``."""
    return Rule(f"{rule.id}@remote", rule.severity, rule.pattern,
                f"on another machine, over ssh: {rule.why}", rule.unless, rule.scope, rule.gate)


def _matches(command: str) -> list[tuple[Rule, re.Match[str], str]]:
    found = _local(command)
    seen = {r.id for r, _, _ in found}
    for script in ssh_scripts(command):
        for rule, match, source in _local(script):
            moved = remote(rule)
            if moved.id not in seen:
                seen.add(moved.id)
                found.append((moved, match, source))
    order = {r.id: i for i, r in enumerate(RULES)}
    return sorted(found, key=lambda t: (t[0].severity != HIGH, "@" in t[0].id, order[t[0].id.split("@")[0]]))


def evidence(command: str, width: int = 160) -> str:
    """The stretch of the command around what the most severe rule matched, on one line."""
    found = _matches(command)
    if not found:
        return command.strip().splitlines()[0][:width] if command.strip() else ""
    _, match, source = found[0]
    at = match.start()
    if source != command:
        # the rule read a narrower view; find the same spot in what was actually typed
        anchor = match.group(0).strip()
        at = command.find(anchor)
        if at == -1:
            at = command.find(anchor[:12].strip()) if anchor[:12].strip() else -1
        if at == -1:
            at = command.find(source[:20].strip()) if source[:20].strip() else -1
        at = max(at, 0)
        source = command
    start = source.rfind("\n", 0, at) + 1
    end = source.find("\n", at)
    line = source[start:] if end == -1 else source[start:end]
    pos = at - start
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
    (re.compile(r"\b(sk|rk|pk)_(live|test)_[A-Za-z0-9]{16,}"), _FULL),
    (re.compile(r"\bhf_[A-Za-z0-9]{30,}"), _FULL),
    (re.compile(r"(?<!\d)\d{8,10}:[A-Za-z0-9_-]{35}(?![A-Za-z0-9_-])"), _FULL),
    (re.compile(r"(?i)((?:x-api-key|api-key|x-auth-token|x-access-token|private-token)\s*:\s*)[^\s'\"]{8,}"),
     r"\1" + _FULL),
    (re.compile(r"(?i)(\"(?:api_?key|apikey|token|access_token|refresh_token|secret|client_secret|password)\"\s*:\s*\")"
                r"[^\"]{4,}(\")"), r"\1" + _FULL + r"\2"),
    (re.compile(r"(\b(?:mysql|mariadb|mysqldump)\b[^\n]*?\s-p)[^\s'\"]{3,}"), r"\1" + _FULL),
    (re.compile(r"(?i)\b(aws_secret_access_key|aws_session_token)(\s+|\s*=\s*)\S{8,}"), r"\1\2" + _FULL),
    (re.compile(r"(?i)(--(?:password|passwd|token|api-key|apikey|secret|access-token)(?:=|\s+))[^\s'\"]{4,}"),
     r"\1" + _FULL),
)


def redact(command: str) -> str:
    """The command with anything that looks like a credential replaced.

    Applied before a command reaches the model or a report, so a summary can
    never repeat a token, even though nothing here leaves the machine.
    """
    for pattern, replacement in _SECRETS:
        command = pattern.sub(replacement, command)
    return command
