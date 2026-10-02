# whatran

What your coding agents ran in your terminal today, explained by a model that runs on your own machine.

![whatran on a made-up working day: three agents, eight flags, and a note from Gemma 4 running locally](assets/whatran.gif)

Coding agents run shell commands all day. Claude Code keeps every one of them in its session files, and since 2026 atuin can record them too (`atuin hook install claude-code`), tagged with the agent that ran them. Then nobody reads any of it: atuin hides agent commands from its search by default, so they don't clutter your history, and the session files are thousands of lines of JSON.

whatran reads that history once a day and answers three questions:

1. **Where did the agents work?** Commands per agent and project, how many failed, how many were stopped before they ran.
2. **What deserves a second look?** `curl | sh`, `rm -rf` on something that isn't a cache, `git push --force`, reading `.env` or `~/.ssh`, `sudo` here or over `ssh`, writing to your `.zshrc`, new packages arriving with install scripts.
3. **What was the agent trying to do?** A short note in plain words, written by [Gemma 4](https://ollama.com/library/gemma4) through Ollama, on your machine.

Your shell history is where tokens typed inline, database URLs with passwords and client names in paths end up. whatran never sends it anywhere: it reads the files read-only, and it refuses a model host that isn't this machine unless you pass `--allow-remote-model`.

## Install

You need Python 3.10 or newer. For the note you also need [Ollama](https://ollama.com) and the model:

```sh
ollama pull gemma4:12b
pipx install git+https://github.com/vinimabreu/whatran
```

There are no dependencies, so `git clone` and `python3 -m whatran` works too. Without Ollama you still get the counts and the flags; only the note is missing.

## Use

```sh
whatran                    # today, since midnight
whatran --since 24h        # or 3d, 1w, 2026-10-01
whatran --lang pt          # the note in Brazilian Portuguese
whatran --all              # flag your own commands too, not only the agents'
whatran --no-model         # counts and flags only
whatran --json             # everything, for another tool
```

It finds its sources by itself:

| Source | What it holds | Setup |
|---|---|---|
| atuin (`~/.local/share/atuin/history.db`) | your commands, plus agent commands when the hook is on | `atuin hook install claude-code` (or `codex`, `opencode`, `pi`) |
| Claude Code sessions (`~/.claude/projects`) | every `Bash` call Claude Code made, with its description and its result | none |

With both present, atuin counts your commands, and the session files fill in Claude Code's when atuin has none of them. `--source atuin` or `--source claude-code` picks one.

To try it without your own history, there is a made-up day:

```sh
python3 examples/make_demo.py demo/history.db
whatran --db demo/history.db
```

## How it decides

The design is a split. Plain code counts, groups and flags; the model only writes the paragraph.

**Rules, not the model, decide what gets flagged.** Each rule in [`whatran/rules.py`](whatran/rules.py) is a regular expression with a reason a person can read, so the same command gets the same answer every day. They look at the part of a command the shell runs here ([`shellview.py`](whatran/shellview.py)): the body of a heredoc fed to Python, a commit message, or a script passed to `ssh` is not code that ran on this machine, so it is not matched as if it were. The script of `bash -c` or `eval` is, and a heredoc fed to a shell is. `rm -rf node_modules __pycache__` is not flagged; `rm -rf migrations` is.

The first version skipped that step. Run on one real working day of mine (555 agent commands), it raised 64 flags, and almost all were wrong: test strings inside heredocs, `sudo` that ran on a server through `ssh`, cache cleanups. The same day now gives three groups.

**Repeated flags are grouped** by agent, project and rule, so 48 `ssh` commands with `sudo` on one server read as one line with `x48`.

**The note is checked before you see it.** The model gets the facts as JSON: counts, the projects, and each flag with its intent and the commands just before and after it in the same session. Its note then has to pass [`check_note`](whatran/digest.py): every command it quotes in backticks must be in the facts, every number it uses must be stated in them, and every `[F1]` it cites must exist. If the note fails, whatran asks once more with the problems listed; if it fails again, the note is not shown and the report says why. The facts never depended on the model, so they are always there.

**Secrets are masked before anything is shown or sent to the model**: GitHub, OpenAI, Anthropic, AWS, Slack and npm tokens, bearer headers, `*_TOKEN=` and `*_PASSWORD=` values, and passwords in connection URLs.

## What it does not do

- It reports after the fact. It does not block a command; atuin and your agent's own permission settings are where that happens.
- Rules read the command text. A script that does something dangerous inside is only as visible as its name, and an unusual spelling of a command can slip past a rule.
- Both sources only see the shell. File edits an agent makes through its own tools are not commands and do not appear.
- It does not explain your own commands, only the agents'. `--all` flags yours, but the note is about the agents.

## Tests

```sh
pip install pytest
python -m pytest
```

178 tests: every rule against commands that should and should not trip it, the agent detection against atuin's own rules (including its exception for a user whose name is an agent's), both readers on databases and session files written the way atuin and Claude Code write them, and the note check against notes that invent commands, numbers and flags. None of them needs Ollama.

## License

MIT
