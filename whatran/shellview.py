"""The part of a command line the shell will actually run.

A command's text holds a lot that is not code: the body of a heredoc fed to
Python, a commit message, a JSON payload, a script passed to ``ssh`` that runs
on another machine. Matching rules against all of it flags test fixtures and
remote work as if they ran here. This module keeps what runs here:

- a heredoc body is dropped unless it is fed to a shell (``bash <<EOF``);
- a quoted string with whitespace in it is blanked, unless it is the script of
  ``sh -c``, ``bash -c``, ``zsh -c`` or ``eval``, which is code and is kept;
- a quoted single token (``"$HOME/.ssh/id_rsa"``) is kept, because a path or
  a flag in quotes is still an argument.

It is a reading aid for the rules, not a shell parser: it does not expand
variables or follow ``source``, and odd quoting can still fool it.
"""

from __future__ import annotations

import re

SHELLS = {"sh", "bash", "zsh", "dash", "ksh", "fish"}
_HEREDOC = re.compile(r"<<(-?)\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\2")
_SEP = re.compile(r"[;&|(]\s*|\n")


def _command_word(text_before: str) -> str:
    """The program of the simple command that ends at ``text_before``."""
    segment = _SEP.split(text_before)[-1].strip()
    words = segment.split()
    while words and (words[0] in {"sudo", "env", "command", "exec", "time", "nohup"} or "=" in words[0]
                     or words[0].startswith("-")):
        words = words[1:]
    return words[0].rsplit("/", 1)[-1] if words else ""


def strip_heredocs(command: str) -> str:
    out, i = [], 0
    while True:
        match = _HEREDOC.search(command, i)
        if not match:
            out.append(command[i:])
            return "".join(out)
        line_end = command.find("\n", match.end())
        if line_end == -1:
            out.append(command[i:])
            return "".join(out)
        dash, tag = match.group(1), match.group(3)
        body_end = None
        pos = line_end + 1
        while pos <= len(command):
            nxt = command.find("\n", pos)
            line = command[pos:] if nxt == -1 else command[pos:nxt]
            if (line.lstrip("\t") if dash else line) == tag:
                body_end = pos
                after = len(command) if nxt == -1 else nxt
                break
            if nxt == -1:
                break
            pos = nxt + 1
        if body_end is None:
            out.append(command[i:])
            return "".join(out)
        feeds_shell = _command_word(command[:match.start()]) in SHELLS
        out.append(command[i:line_end + 1])
        if feeds_shell:
            out.append(command[line_end + 1:body_end])
        i = after


def _is_script_slot(before: str) -> bool:
    tail = before.rstrip().split()
    if not tail:
        return False
    if tail[-1] == "eval":
        return True
    return len(tail) >= 2 and tail[-1] == "-c" and tail[-2].rsplit("/", 1)[-1] in SHELLS


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
            j = i + 1
            while j < n and command[j] != ch:
                j += 2 if ch == '"' and command[j] == "\\" else 1
            inner = command[i + 1:j]
            if _is_script_slot("".join(out)):
                out.append(" " + blank_quotes(inner) + " ")
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
