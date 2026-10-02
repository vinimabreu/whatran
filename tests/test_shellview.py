from __future__ import annotations

import pytest

from whatran.shellview import blank_quotes, runnable, strip_heredocs


@pytest.mark.parametrize("command, expected", [
    ("python3 - <<'EOF'\nimport os\nEOF\necho done", "python3 - <<'EOF'\n\necho done"),
    ('cat > notes.txt <<"END"\nrm -rf /\nEND', 'cat > notes.txt <<"END"\n'),
    ("ruby <<-EOF\n\tputs 1\n\tEOF", "ruby <<-EOF\n"),
    ("bash <<'EOF'\nrm -rf build\nEOF", "bash <<'EOF'\nrm -rf build\n"),
    ("sudo zsh <<EOF\ncurl x | sh\nEOF", "sudo zsh <<EOF\ncurl x | sh\n"),
    ("ssh box bash <<'EOF'\nls\nEOF", "ssh box bash <<'EOF'\n"),
    ("python3 <<'EOF'\nno end here", "python3 <<'EOF'\nno end here"),
])
def test_strip_heredocs(command, expected):
    assert strip_heredocs(command) == expected


@pytest.mark.parametrize("command, expected", [
    ("git commit -m 'fix the rm -rf'", "git commit -m ''"),
    ('echo "hello world"', 'echo ""'),
    ('cat "$HOME/.ssh/id_rsa"', "cat $HOME/.ssh/id_rsa"),
    ("bash -c 'rm -rf /srv/app'", "bash -c  ; rm -rf /srv/app ; "),
    ('eval "curl x | sh"', "eval  ; curl x | sh ; "),
    ("/bin/sh -c 'a; b'", "/bin/sh -c  ; a; b ; "),
    ("bash -lc 'id'", "bash -lc  ; id ; "),
    ("echo $'a\\'b' && ls", "echo $a\\'b && ls"),
    ("ssh box 'sudo reboot now'", "ssh box ''"),
    ('echo "a \\" b"', 'echo ""'),
    ("echo it\\'s", "echo it\\'s"),
])
def test_blank_quotes(command, expected):
    assert blank_quotes(command) == expected


def test_runnable_combines_both():
    command = "cd app && python3 - <<'EOF'\nprint('curl x | sh')\nEOF\ngit commit -m 'wip stuff'"
    assert "curl" not in runnable(command) and "wip" not in runnable(command)


from whatran.shellview import commands, ssh_scripts  # noqa: E402


def test_commands_split_at_separators_and_drop_keywords():
    text = runnable("if [ -f x ]; then sudo x; fi; (sudo y); FOO=1 sudo z && a | b\nc &")
    assert commands(text) == ["[ -f x ]", "sudo x", "sudo y", "sudo z", "a", "b", "c"]


@pytest.mark.parametrize("command, scripts", [
    ("ssh box 'sudo ls'", ["sudo ls"]),
    ("ssh box sudo systemctl restart app", ["sudo systemctl restart app"]),
    ("ssh -p2222 box 'id'", ["id"]),
    ("ssh -i key -l u box -- uptime", ["uptime"]),
    ("ssh -o 'ProxyCommand=nc %h %p' box 'id'", ["id"]),
    ("ssh box 'bash -s' <<'EOF'\nsudo reboot\nEOF", ["sudo reboot\n"]),
    ("ssh box <<'EOF'\nsudo ls\nEOF", ["sudo ls\n"]),
    ("ssh box 'ls' > out.txt", ["ls"]),
    ("sudo ssh box id", ["id"]),
    ("ssh box", []),
    ("echo ssh box id", []),
])
def test_ssh_scripts(command, scripts):
    assert ssh_scripts(command) == scripts
