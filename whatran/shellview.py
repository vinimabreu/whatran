"""The part of a command line the shell will actually run, one simple command at a time.

A command's text holds a lot that is not code: the body of a heredoc fed to
Python, a commit message, a JSON payload, a script passed to ``ssh`` that runs
on another machine. Matching rules against all of it flags test fixtures and
remote work as if they ran here. This module narrows the text down:

- a heredoc body is dropped unless it is fed to a shell (``bash <<EOF``), and a
  body fed to ``ssh`` is kept apart as a script for another machine;
- a quoted string with whitespace in it is blanked, unless it is the script of
  ``sh -c``, ``bash -lc``, ``eval`` and the like, which is code and is kept;
- a quoted single token (``"$HOME/.ssh/id_rsa"``) is kept, because a path or a
  flag in quotes is still an argument;
- the result is split into simple commands at unquoted ``;``, ``&&``, ``||``,
  ``|``, ``&`` and newlines, with leading keywords (``if``, ``then``, ``(``...)
  and ``VAR=value`` assignments taken off, so a rule never spans two commands.

It is a reading aid for the rules, not a shell parser: it does not expand
variables or follow ``source``, and odd quoting can still fool it.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
_PIPED_TO_SHELL = re.compile(r"\|\s*(sudo\s+(-\S+\s+)*)?(\S*/)?(ba|z|da|k)?sh\b")
_REDIRECT = re.compile(r"^(\d*|&)(>>?|<<?<?)")
_KEYWORDS = {"if", "then", "do", "else", "elif", "while", "until", "!", "{", "}", "(", ")",
             "time", "nohup", "exec", "command", "builtin", "fi", "done", "esac"}
_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_SSH_OPTS_WITH_ARG = set("bcDEeFIiJLlmOopQRSWw")
_WRAPPERS = {"sudo", "doas", "env", "timeout", "nice", "nohup", "caffeinate", "time"}


@dataclass(frozen=True)
class Heredoc:
    program: str
    body: str


def _program(words: list[str]) -> str:
    """The program a simple command runs, past keywords, assignments and wrappers."""
    i = 0
    while i < len(words):
        w = words[i]
        if w in _KEYWORDS or _ASSIGN.match(w):
            i += 1
        elif w in _WRAPPERS:
            i += 1
            while i < len(words) and (words[i].startswith("-") or _ASSIGN.match(words[i])):
                i += 2 if words[i] in {"-u", "-g", "-C", "-h", "-n", "-s", "-k"} else 1
            if w == "timeout" and i < len(words):
                i += 1
        else:
            return w.rsplit("/", 1)[-1]
    return ""


def _heredocs(command: str) -> tuple[str, list[Heredoc]]:
    """The command without heredoc bodies, and the bodies with the program fed by each."""
    out, docs, i = [], [], 0
    while True:
        match = _HEREDOC.search(command, i)
        line_end = command.find("\n", match.end()) if match else -1
        if not match or line_end == -1:
            out.append(command[i:])
            return "".join(out), docs
        dash, tag = match.group(1), match.group(3)
        pos, body_end, after = line_end + 1, None, None
        while True:
            nxt = command.find("\n", pos)
            line = command[pos:] if nxt == -1 else command[pos:nxt]
            if (line.lstrip("\t") if dash else line) == tag:
                body_end, after = pos, (len(command) if nxt == -1 else nxt)
                break
            if nxt == -1:
                break
            pos = nxt + 1
        if body_end is None:
            out.append(command[i:])
            return "".join(out), docs
        head = command[:match.start()]
        segment = re.split(r"(?:\|\||&&|[;|&\n])", head)[-1]
        program = _program(segment.split())
        docs.append(Heredoc(program, command[line_end + 1:body_end]))
        out.append(command[i:line_end + 1])
        if program in SHELLS or _PIPED_TO_SHELL.search(command[match.end():line_end]):
            out.append(command[line_end + 1:body_end])
        i = after


def strip_heredocs(command: str) -> str:
    return _heredocs(command)[0]


def _is_script_slot(before: str) -> bool:
    tail = before.rstrip().split()[-4:]
    if not tail:
        return False
    if tail[-1] == "eval":
        return True
    return bool(re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", tail[-1])) and any(
        t.rsplit("/", 1)[-1] in SHELLS for t in tail[:-1])


def _quoted_end(command: str, i: int) -> int:
    """Index of the closing quote for the quote opening at ``i`` (len if unclosed)."""
    q, n = command[i], len(command)
    ansi = q == "'" and i > 0 and command[i - 1] == "$"
    j = i + 1
    while j < n and command[j] != q:
        j += 2 if command[j] == "\\" and (q == '"' or ansi) else 1
    return min(j, n)


def blank_quotes(command: str) -> str:
    out: list[str] = []
    i, n = 0, len(command)
    while i < n:
        ch = command[i]
        if ch == "\\" and i + 1 < n:
            out.append(command[i:i + 2])
            i += 2
            continue
        if ch in "'\"":
            j = _quoted_end(command, i)
            inner = command[i + 1:j]
            if _is_script_slot("".join(out[-80:])):
                out.append(" ; " + blank_quotes(inner) + " ; ")
            elif re.search(r"\s", inner):
                out.append(ch + ch)
            else:
                out.append(inner)
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def runnable(command: str) -> str:
    """The command with data and other-machine scripts taken out; see the module doc."""
    return blank_quotes(strip_heredocs(command))


def split(text: str) -> list[str]:
    """Pipelines at unquoted ``;``, ``&&``, ``||``, ``&`` and newlines (pipes kept inside)."""
    parts, cur, i, n = [], [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "\\" and i + 1 < n:
            cur.append(text[i:i + 2])
            i += 2
            continue
        if ch in "'\"":
            j = _quoted_end(text, i)
            cur.append(text[i:j + 1])
            i = j + 1
            continue
        two = text[i:i + 2]
        if two in ("&&", "||"):
            parts.append("".join(cur))
            cur, i = [], i + 2
            continue
        if ch in ";\n" or (ch == "&" and text[i - 1:i] not in (">", "<") and text[i + 1:i + 2] != ">"):
            parts.append("".join(cur))
            cur, i = [], i + 1
            continue
        cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def pipe_parts(pipeline: str) -> list[str]:
    """The commands of one pipeline, split at unquoted single ``|``."""
    parts, cur, i, n = [], [], 0, len(pipeline)
    while i < n:
        ch = pipeline[i]
        if ch == "\\" and i + 1 < n:
            cur.append(pipeline[i:i + 2])
            i += 2
            continue
        if ch in "'\"":
            j = _quoted_end(pipeline, i)
            cur.append(pipeline[i:j + 1])
            i = j + 1
            continue
        if ch == "|" and pipeline[i + 1:i + 2] != "|":
            parts.append("".join(cur))
            cur, i = [], i + 1
            continue
        cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def strip_prefix(simple: str) -> str:
    """A simple command without leading keywords and ``VAR=value`` assignments."""
    words = simple.split()
    while words and (words[0] in _KEYWORDS or _ASSIGN.match(words[0])):
        words = words[1:]
    rest = " ".join(words)
    return rest.lstrip("({ ").rstrip(")} ") if rest.startswith(("(", "{")) else rest


def strip_sudo(simple: str) -> str:
    """The command a wrapper (``sudo``, ``env``, ``timeout``, ``nice``...) runs, its options taken off."""
    return " ".join(_unwrap(simple.split()))


def commands(text: str) -> list[str]:
    """Every simple command in ``text`` (already runnable), prefixes stripped."""
    return [c for p in split(text) for c in (strip_prefix(x) for x in pipe_parts(p)) if c]


def _remote_script(words: list[str]) -> str | None:
    """The script of ``ssh [options] host [--] script...``, or None when there is none."""
    i = 1
    while i < len(words):
        w = words[i]
        if w == "--":
            i += 1
            break
        if w.startswith("-") and len(w) > 1:
            if len(w) == 2 and w[1] in _SSH_OPTS_WITH_ARG:
                i += 2
            else:
                i += 1
            continue
        i += 1  # the host
        if i < len(words) and words[i] == "--":
            i += 1
        break
    else:
        return None
    rest = []
    skip = False
    for w in words[i:]:
        if skip:
            skip = False
            continue
        if _REDIRECT.match(w):
            skip = w in {"<", ">", ">>", "<<", "<<<", "2>", "2>>", "&>"}
            continue
        rest.append(w)
    return " ".join(rest) if rest else None


def _unwrap(words: list[str]) -> list[str]:
    """Words without leading keywords, assignments, and ``sudo``/``env`` with their options."""
    while words and (words[0] in _KEYWORDS or _ASSIGN.match(words[0])):
        words = words[1:]
    while words and words[0] in _WRAPPERS:
        wrapper, words = words[0], words[1:]
        while words and (words[0].startswith("-") or _ASSIGN.match(words[0])):
            words = words[2:] if words[0] in {"-u", "-g", "-C", "-h", "-n", "-s", "-k"} else words[1:]
        if wrapper == "timeout" and words:
            words = words[1:]
    return words


def ssh_scripts(command: str) -> list[str]:
    """The scripts a command sends to other machines over ``ssh``.

    Covers ``ssh host '<script>'``, ``ssh host cmd args``, ``ssh -p2222 host -- ...``
    and a heredoc fed to ``ssh`` (``ssh host 'bash -s' <<EOF``, ``ssh host <<EOF``).
    """
    text, docs = _heredocs(command)
    scripts = [d.body for d in docs if d.program == "ssh"]
    for pipeline in split(text):
        for part in pipe_parts(pipeline):
            try:
                words = shlex.split(part, comments=False, posix=True)
            except ValueError:
                continue
            words = _unwrap(words)
            if not words or words[0].rsplit("/", 1)[-1] != "ssh":
                continue
            script = _remote_script(words)
            if script and not (script in ("bash -s", "sh -s", "bash", "sh") and any(
                    d.program == "ssh" for d in docs)):
                scripts.append(script)
    return scripts


def is_ssh(simple: str) -> bool:
    words = strip_sudo(simple).split()
    return bool(words) and words[0].rsplit("/", 1)[-1] == "ssh"
