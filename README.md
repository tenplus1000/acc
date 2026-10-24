# Agent Context Controller

English | [繁體中文](README.zh-TW.md)

A local tool that lets Codex CLI and Claude Code read state you maintain yourself on every
`UserPromptSubmit`: **Rapport** (how you want to work together right now) and **Goals** (your
ultimate goal, the project's goal, and this conversation's current Objective).
Pure Python standard library, no pip dependencies.

## The idea

Coding agents forget what you are trying to do between turns, and drift when a conversation runs long.
acc keeps that state outside the conversation, in plain files you can edit, and injects it at the start
of every turn:

- **Rapport**: how the agent should work with you at the moment (a preset and a level you can switch).
- **Goals**, in layers:
  - **Ultimate Goal / Definition**: what "done well" means for you, across all work.
  - **Super Objective**: the long-term change this project is after in its current phase.
  - **Objective**: the one thing this conversation is doing right now, with Success Criteria.

The agent updates the Objective itself through the `acc` CLI (set, done, fail, park, revise, amend),
following rules you install into its global instruction file. You can see and correct everything in a
local web panel, and every change is kept in an append-only event log.

> The web panel and the default rule templates are written in Traditional Chinese. The rules live in
> `<data dir>/templates/rules.md` and can be rewritten in any language.

## Install

Symlink `bin/acc` onto your PATH (for example `~/.local/bin`):

```bash
ln -s "$(pwd)/bin/acc" ~/.local/bin/acc   # run from the repo root
```

By default the data lives in `.agent-context` under your Windows user folder
(`/mnt/c/Users/<your name>/.agent-context` inside WSL, so both a Windows browser and agents running in
WSL can reach it); outside WSL it is `~/.agent-context`. The first choice is remembered in
`~/.config/acc/root`; edit that file, or set `ACC_HOME` (which wins), to move it. The folder and its
default files are created on first use.

## Connecting an agent

The per-turn hook injects **state only**. How the agent should update that state from what you say is
written in `<data dir>/templates/rules.md`, which you install into the agent's global instruction file.

### Claude Code

1. `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      { "hooks": [ { "type": "command", "command": "acc hook claude", "timeout": 10 } ] }
    ]
  },
  "permissions": { "allow": ["Bash(acc objective:*)", "Bash(acc super:*)"] }
}
```

2. `acc rules install claude`: adds two import lines to `~/.claude/CLAUDE.md`, for `rules.md` and
   `goals/ultimate.md`. Both are live imports, so edits are picked up by new conversations without
   reinstalling.

### Codex CLI

1. `~/.codex/hooks.json`:

```json
{"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "acc hook codex", "timeout": 10}]}]}}
```

2. `~/.codex/config.toml`: let the sandbox write to the data folder, or `acc objective …` will fail when
   the agent runs it:

```toml
[sandbox_workspace_write]
writable_roots = ["/mnt/c/Users/<your name>/.agent-context"]
```

3. `acc rules install codex`: copies the full text of `rules.md` and the current Ultimate
   (`goals/ultimate.md`) into `~/.codex/AGENTS.md`. Codex has no import syntax, so rerun this after
   changing either file.
4. Open Codex and review and trust the hook in its UI; it will not run until you do.

Rules, the hook and the Ultimate Goal / Definition are loaded when a conversation starts; reopen
conversations that are already running. A hook error never blocks your message: it is written to
`<data dir>/logs/hook.log`, nothing is printed, and the hook exits 0.

Changing the Ultimate later does not require reopening a conversation: each turn compares it with the
version that conversation started with, and only when it changed does it attach the full updated text
once (and remember that it did). When you save the Ultimate in the web panel and Codex was installed
before, `install` is rerun automatically so new Codex conversations get the new version too.

## Web panel

```bash
acc serve            # http://localhost:8765; override with --port / --host
```

- **Conversations** (top): one card per conversation, with a colour bar, nickname, short id and last
  activity. Expanded, a card shows:
  - this conversation's Rapport (or "follow the default") and its project
  - the current Objective and Success Criteria with their edit history, and buttons to edit, mark
    achieved, park or mark failed
  - the parked list: drag or move up and down, swap one with the current Objective, remove
  - a timeline of closed Objectives: each working and parked stretch, the Rapport that was sent at the
    time, and reopening
  - archiving; conversations with no message for `idle_hours` (default 6) move to "idle"
- **Projects**: each project's Super Objective, its history, and suggestions from agents to accept or
  reject.
- **Ultimate** (bottom): the Ultimate Goal / Definition and their history.
- **Floating window** (bottom right): a work / rest switch with its start time (your own record; it
  never changes what is injected), the default Rapport, a global "inject Rapport" switch, and a preset
  editor for each level's prompt.
- **Per-conversation Rapport switch**: besides the global switch, each card can follow the global
  setting, force it on, or force it off. When off, that turn's context has no `<Rapport>` and the
  timeline and work log mark it "not sent".
- **Copy work log**: copies everything by default; the menu next to it narrows it to the latest work
  session or a chosen range, and the "view" panel shows the same range.

## CLI

```bash
acc root                                    # print the data root
acc context --session ID [--cwd PATH]       # preview the context (does not touch the session file)
acc objective show --session ID
acc objective set  --session ID "<objective>" --criteria "<success criteria>"
acc objective done --session ID --note "<evidence>"
acc objective fail --session ID --note "<why this path did not work>"
acc objective park --session ID --reason "<what the user said>"
acc super suggest  --session ID "<new Super Objective>" --reason "<why>"
acc super show     --session ID
acc objective revise --session ID ["<new objective>"] [--criteria "<new criteria>"] --reason "<why>"
acc objective log    --session ID           # this session's work log (Markdown)
acc objective log    --session ID --latest-work          # only the latest work session
acc objective log    --session ID --work-from N --work-to M  # only work sessions N..M
acc objective list   --session ID           # current / parked / closed (last 10), with P<n>/H<n> ids and oids
acc objective amend  --session ID --id <P1|H2|oid> --to achieved|failed|parked|active --reason "<why>" [--note "<new note>"]
acc work status                             # working or resting, which work session, since when
acc work start                              # back to working (starts a new work session)
acc work stop                               # switch to resting
acc rules show                              # print templates/rules.md
acc rules install claude|codex [--target P] # write the rules into ~/.claude/CLAUDE.md or ~/.codex/AGENTS.md
acc serve [--port N] [--host 127.0.0.1]
```

Each turn injects state only; which judgement the agent makes and which command it uses is written in
`templates/rules.md`, which `acc rules install` puts into the agent's global instruction file (Claude
imports it, Codex gets a full copy, so rerun `install` for Codex after editing `rules.md`).

`objective set` fails while the session already has a current Objective: close it with `done` or
`fail`, or `park` it, before setting the next one. Edits made in the web panel are your own actions and
can change any field directly.

`acc objective park` moves the current Objective, whole, onto this session's parked list (it is not
closed and does not enter the history) and leaves no current Objective, so the agent can `set` a new
one. Parked Objectives are **not** injected. When `done` or `fail` later closes the current Objective
and the parked list is not empty, the first parked one is resumed automatically as the new current
Objective (the CLI prints an extra "resumed" line). In the web panel you can also swap a parked one
with the current one, reorder the list, or remove an entry (recorded in the history as dropped).

`acc objective amend` corrects an earlier `done`, `fail` or `park` that was the wrong call (achieved
recorded as failed, closed when it was only parked, and so on). Find its short id with
`acc objective list` (parked: P1, P2…; the last 10 closed: H1, H2…; an oid also works), then run
`amend --id <id> --to <state> --reason "<why the earlier call was wrong>"`. The CLI (the agent) must give
`--reason`; the web panel (you) does not have to. Without `--note` the old note is kept. Every
correction is recorded as an `objective.amended` event, and the work log and the web timeline show it
as corrected.

`acc super suggest` only files a suggestion awaiting your decision (stored in
`goals/projects/<key>.suggestions.json`); it does **not** change the Super Objective, and agents have no
command that does. The Projects section of the web panel lists pending suggestions; "accept" applies one
(and records `super.changed`), "reject" marks it rejected. `acc super show` prints the current Super
Objective and the pending suggestions for that session's project.

## Event log

`<ACC_HOME>/events.jsonl` is append-only: every operation that changes state (Rapport changes, Objective
set / closed / edited, Super Objective changes and suggestions, sessions created / archived / renamed…)
adds one JSON line of the form `{"at": "<ISO time>", "type": "<event type>", "actor":
"user"|"agent"|"hook", ...}`. A `turn` event is recorded on every hook call, whether or not anything
changed since the last turn, with the Rapport / Objective / Super Objective state at that moment, but
never the prompt text (only its length, `prompt_chars`).

`<ACC_HOME>/.snapshots.json` holds hashes for detecting outside edits (`goals/ultimate.md`,
`goals/projects/*.md`, `templates/*.md`, `rapport/presets/*/*.md`); each hook call compares them, and a
change that acc did not make itself is recorded as an `external_edit` event. The log is write-only for
now, with no dedicated statistics view; the Ultimate and Super Objective histories in the web panel are
read straight from it.

## Development / tests

```bash
python3 -m unittest discover -s tests
```

## License

MIT, see [LICENSE](LICENSE). If Agent Context Controller helps your work, a mention or a link back is appreciated.
