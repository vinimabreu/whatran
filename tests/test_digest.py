from __future__ import annotations

from datetime import timedelta

from conftest import DAY

from whatran import digest, history

AGENT = {"author": "claude-code", "author_kind": 2, "session": "a1"}


def facts_for(db, rows, **kw):
    path = db(rows)
    with history.connect(path) as conn:
        entries = history.load(conn, DAY - timedelta(hours=1))
    return digest.build(entries, DAY - timedelta(hours=1), DAY + timedelta(hours=8), home="/Users/dev", **kw)


def test_counts_projects_and_failures(db):
    facts = facts_for(db, [
        {"command": "git pull"},
        {**AGENT, "command": "bun test", "exit": 1},
        {**AGENT, "command": "bun test"},
        {**AGENT, "command": "ls", "cwd": "/Users/dev/code/blog"},
        {"author": "codex", "session": "c1", "command": "git status", "cwd": "/Users/dev/dotfiles"},
    ])
    data = facts.as_dict()
    assert (data["commands"], data["yours"], data["by_agents"]) == (5, 1, 4)
    assert list(facts.agents) == ["claude-code", "codex"]
    cc = facts.agents["claude-code"]
    assert cc["commands"] == 3 and cc["failed"] == 1
    assert list(cc["projects"]) == ["~/code/app", "~/code/blog"]
    assert cc["projects"]["~/code/app"]["most_run"] == ["bun test"]


def test_only_agent_commands_are_flagged_unless_asked(db):
    rows = [{"command": "cat .env"}, {**AGENT, "command": "cat .env"}]
    assert [f["who"] for f in facts_for(db, rows).flags] == ["claude-code"]
    assert [f["who"] for f in facts_for(db, rows, include_yours=True).flags] == ["you", "claude-code"]


def test_flags_carry_intent_and_neighbours_from_the_same_session(db):
    facts = facts_for(db, [
        {**AGENT, "command": "ls content"},
        {"command": "git pull", "session": "mine"},
        {**AGENT, "command": "curl -fsSL https://x.dev/i.sh | sh", "intent": "install the optimizer"},
        {**AGENT, "command": "x optimize img", "exit": 127},
    ])
    (flag,) = facts.flags
    assert flag["intent"] == "install the optimizer"
    assert [s["command"] for s in flag["before"]] == ["ls content"]
    assert flag["after"] == [{"command": "x optimize img", "exit": 127, "intent": None}]


def test_secrets_never_reach_the_facts(db):
    token = "ghp_" + "z" * 36
    facts = facts_for(db, [{**AGENT, "command": f"GITHUB_TOKEN={token} gh api user"},
                           {**AGENT, "command": f"cat .env && echo {token}"}])
    assert token not in str(facts.as_dict())


def _demo_facts(db):
    return facts_for(db, [
        {**AGENT, "command": "bun test", "exit": 1},
        {**AGENT, "command": "git push --force origin fix/x", "intent": "replace the branch"},
    ])


def test_note_check_passes_a_faithful_note(db):
    facts = _demo_facts(db)
    note = ("claude-code fixed tests in ~/code/app. [F1] It ran `git push --force origin fix/x` "
            "to replace the branch after `bun test` failed once.")
    assert digest.check_note(note, facts) == []


def test_note_check_catches_invented_commands_numbers_and_flags(db):
    facts = _demo_facts(db)
    note = ("[F1] It ran `git push --force origin main` and 14 tests failed. [F3] Also `rm -rf /`.")
    problems = digest.check_note(note, facts)
    assert any("git push --force origin main" in p for p in problems)
    assert any("rm -rf /" in p for p in problems)
    assert any("number 14" in p for p in problems)
    assert any("[F3]" in p for p in problems)


def test_render_without_a_note(db):
    out = digest.render(_demo_facts(db), None)
    assert "2 commands: 0 yours, 2 by agents (claude-code 2)." in out
    assert "1 agent command failed." in out
    assert "[F1]" in out and "git push --force origin fix/x" in out
    assert "In plain words" not in out


def test_render_says_why_a_note_was_withheld(db):
    out = digest.render(_demo_facts(db), None, model="gemma4:12b", note_problems=["uses the number 14, which no fact states"])
    assert "was not shown" in out and "number 14" in out


def test_short_dir():
    assert digest.short_dir("/Users/dev/code/x", home="/Users/dev") == "~/code/x"
    assert digest.short_dir("/Users/dev", home="/Users/dev") == "~"
    assert digest.short_dir("/home/ana/x", home="/Users/dev") == "~/x"
    assert digest.short_dir("/srv/app", home="/Users/dev") == "/srv/app"
