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
    head: str
    """The simple command the heredoc is fed to, as typed."""


def join_lines(command: str) -> str:
    """Backslash-newline continuations joined, as the shell joins them."""
    return command.replace("\\\n", " ")


def _base(word: str) -> str:
    """``git`` for ``/usr/bin/git`` and ``pip`` for ``.venv/bin/pip``; a script of the
    repository (``./crontab``, ``scripts/set``) keeps its path, so it is not taken for
    the system program of the same name."""
    if "/" not in word or not (word.startswith(("/", "~")) or "bin/" in word):
        return word
    return word.rsplit("/", 1)[-1]


def _program(words: list[str]) -> str:
    """The program a simple command runs, past keywords, assignments and wrappers."""
    i = 0
    while i < len(words):
        w = words[i]
        if w in _KEYWORDS or _ASSIGN.match(w):
            i += 1
        elif _base(w) in _WRAPPERS:
            i += 1
            while i < len(words) and (words[i].startswith("-") or _ASSIGN.match(words[i])):
                i += 2 if words[i] in {"-u", "-g", "-C", "-h", "-n", "-s", "-k"} else 1
            if w == "timeout" and i < len(words):
                i += 1
        else:
            return _base(w)
    return ""


def _open_quote(segment: str) -> bool:
    """True when ``segment`` ends inside a quoted string."""
    i, n = 0, len(segment)
    while i < n:
        ch = segment[i]
        if ch == "\\":
            i += 2
            continue
        if ch in "'\"":
            j = _quoted_end(segment, i)
            if j >= n:
                return True
            i = j + 1
            continue
        i += 1
    return False


def heredocs(command: str) -> tuple[str, list[Heredoc]]:
    """The command without heredoc bodies, and the bodies with the program fed by each.

    A ``<<`` inside quotes is text (often a heredoc in a script sent to ``ssh``), not a
    heredoc of this shell, and is left alone.
    """
    out, docs, i = [], [], 0
    while True:
        match = _HEREDOC.search(command, i)
        # quote state counts from where the last heredoc ended: its body was data
        while match and _open_quote(command[i:match.start()]):
            match = _HEREDOC.search(command, match.end())
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
        docs.append(Heredoc(program, command[line_end + 1:body_end], segment.strip()))
        out.append(command[i:line_end + 1])
        if program in SHELLS or _PIPED_TO_SHELL.search(command[match.end():line_end]):
            out.append(command[line_end + 1:body_end])
        i = after


def strip_heredocs(command: str) -> str:
    return heredocs(command)[0]


def _is_script_slot(before: str) -> bool:
    tail = before.rstrip().split()[-4:]
    if not tail:
        return False
    segment = re.split(r"(?:\|\||&&|[;|&\n])", before)[-1]
    if _program(segment.split()) == "ssh":
        return False  # a script for another machine, not for this shell
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
            if _is_script_slot("".join(out[-400:])):
                out.append(" ; " + blank_quotes(inner) + " ; ")
            elif re.search(r"\s", inner):
                out.append(ch + ch)
            elif re.search(r"['\"\\]", inner):
                out.append(ch + inner + ch)  # "can't": keep the quotes so the apostrophe stays inside
            else:
                out.append(inner)
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def strip_comments(command: str) -> str:
    """Unquoted ``# ...`` comments removed, so an apostrophe in one can't open a quote."""
    out, i, n = [], 0, len(command)
    while i < n:
        ch = command[i]
        if ch == "\\" and i + 1 < n:
            out.append(command[i:i + 2])
            i += 2
            continue
        if ch in "'\"":
            j = _quoted_end(command, i)
            out.append(command[i:j + 1])
            i = j + 1
            continue
        if ch == "#" and (i == 0 or command[i - 1] in " \t\n;&|("):
            nl = command.find("\n", i)
            i = n if nl == -1 else nl
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def runnable(command: str) -> str:
    """The command with data and other-machine scripts taken out; see the module doc."""
    return blank_quotes(strip_comments(strip_heredocs(join_lines(command))))


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
        if ch == "#" and (i == 0 or text[i - 1] in " \t\n;&|("):
            nl = text.find("\n", i)
            i = n if nl == -1 else nl  # a comment runs to the end of the line
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
    rest = rest.lstrip("({ ").rstrip(")} ") if rest.startswith(("(", "{")) else rest
    head, sep, tail = rest.partition(" ")
    return _base(head) + sep + tail  # /usr/bin/git and .venv/bin/pip are git and pip


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
    if not rest:
        return None
    # one word is the script as the remote shell receives it; several words are joined
    # by ssh with spaces, so quoting is kept to let `bash -c "sudo ls"` stay a -c script
    return rest[0] if len(rest) == 1 else shlex.join(rest)


def _unwrap(words: list[str]) -> list[str]:
    """Words without leading keywords, assignments, and ``sudo``/``env`` with their options."""
    while words and (words[0] in _KEYWORDS or _ASSIGN.match(words[0])):
        words = words[1:]
    while words and _base(words[0]) in _WRAPPERS:
        wrapper, words = _base(words[0]), words[1:]
        while words and (words[0].startswith("-") or _ASSIGN.match(words[0])):
            words = words[2:] if words[0] in {"-u", "-g", "-C", "-h", "-n", "-s", "-k"} else words[1:]
        if wrapper == "timeout" and words:
            words = words[1:]
    if words:
        words = [_base(words[0]), *words[1:]]
    return words


_READS_STDIN_AS_SCRIPT = {"", "bash", "sh", "zsh", "bash -s", "sh -s", "zsh -s", "bash -l", "sudo bash", "sudo bash -s",
                          "sudo sh", "sudo sh -s"}


def _ssh_words(part: str) -> list[str] | None:
    try:
        words = _unwrap(shlex.split(part, comments=True, posix=True))
    except ValueError:
        return None
    return words if words and words[0] == "ssh" else None


def ssh_scripts(command: str) -> list[str]:
    """The scripts a command sends to other machines over ``ssh``.

    Covers ``ssh host '<script>'``, ``ssh host cmd args``, ``ssh -p2222 host -- ...``
    and a heredoc fed to ``ssh``: ``ssh host <<EOF`` and ``ssh host 'bash -s' <<EOF`` run
    the body as a script; ``ssh host 'cat > f' <<EOF`` or ``ssh host psql <<EOF`` hand the
    body to that remote program, which is what the rules then see.
    """
    command = join_lines(command)
    text, docs = heredocs(command)
    scripts = []
    for doc in docs:
        words = _ssh_words(doc.head.split("<<", 1)[0])
        if words is None:
            continue
        remote = _remote_script(words) or ""
        if remote in _READS_STDIN_AS_SCRIPT:
            scripts.append(doc.body)
        else:
            scripts.append(f"{remote} <<'WHATRAN_EOF'\n{doc.body}WHATRAN_EOF")
    for pipeline in split(text):
        for part in pipe_parts(pipeline):
            if "<<" in blank_quotes(part):
                continue  # a heredoc of this shell, fed to ssh: handled above
            words = _ssh_words(part)
            script = _remote_script(words) if words else None
            if script:
                scripts.append(script)
    return scripts


def is_ssh(simple: str) -> bool:
    words = strip_sudo(strip_prefix(simple)).split()
    return bool(words) and words[0].rsplit("/", 1)[-1] == "ssh"
