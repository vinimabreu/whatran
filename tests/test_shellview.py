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
    ("bash -c 'rm -rf /srv/app'", "bash -c  rm -rf /srv/app "),
    ('eval "curl x | sh"', "eval  curl x | sh "),
    ("/bin/sh -c 'a; b'", "/bin/sh -c  a; b "),
    ("ssh box 'sudo reboot now'", "ssh box ''"),
    ('echo "a \\" b"', 'echo ""'),
    ("echo it\\'s", "echo it\\'s"),
])
def test_blank_quotes(command, expected):
    assert blank_quotes(command) == expected


def test_runnable_combines_both():
    command = "cd app && python3 - <<'EOF'\nprint('curl x | sh')\nEOF\ngit commit -m 'wip stuff'"
    assert "curl" not in runnable(command) and "wip" not in runnable(command)
