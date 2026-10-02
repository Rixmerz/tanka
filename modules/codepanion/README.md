# Codepanion

A counterpart that watches your Claude Code sessions, asks the question that makes you explain, remembers what was left pending, and speaks first when one of **your** lenses says so. It never touches code. Design and reasoning: [`docs/codepanion.md`](../../docs/codepanion.md).

The module ships the mechanism and no role. You build the role, meaning the lenses, with `/new-codepanion` in `tanka dev`, and backtest it against your own past sessions before it goes live.

## Setup

```bash
tanka codepanion setup duck ~/code/my-project     # everything below in one command, then a self-test
```

`setup` creates the workspace if it does not exist, installs the module, watches the project, installs the tap, adds the `watch` trigger, and leaves the automation daemon running as a login service (`--no-service` starts a plain background daemon instead). Then it checks, with no model and nothing sent, that the configuration passes, that the tap writes the feed (with a synthetic session it deletes afterwards), that the daemon runs, and that a desktop notification shows. Run it again to fix what failed: it skips what is done. **Restart the Claude Code sessions already open** in the project: hooks reach new sessions only.

The same steps by hand:

```bash
tanka start duck                                  # a workspace for it: its persona, its language
tanka install codepanion duck                      # the tools, codepanion.json, an empty lenses folder
tanka codepanion watch add ~/code/my-project duck  # which projects it may see (repeat per project)
tanka codepanion tap install                       # observe-only hooks in your normal Claude Code
tanka trigger add duck watch --on codepanion:* --budget 0.15 "React to the signal with your lenses."
tanka automation service install                  # keep the daemon running across logins
```

Then, optionally: `tanka dev duck` → `/new-codepanion` for lenses of your own (it backtests them against your past sessions), `tanka codepanion recap duck on` for the end-of-session recap, and `tanka codepanion statusline install` for 🦆 N in your status line.

Read what it said with `tanka codepanion notes`, and rate a note with `tanka codepanion rate <id> good|bad`. Or open the page: `tanka ui duck` (`/page` in `tanka dev`; [`docs/page.md`](../../docs/page.md)). There the codepanion adds three tabs, its notes with their 👍/👎, the watched sessions with their timelines and the signals they crossed, and the lenses with their check status and ratings, and it speaks in the chat on its own: its notes and its tuning proposals show there between your messages. The chat itself, Your day (the desk, which it needs: what a session leaves open goes on your cards) and the daemon's health belong to the page.

## How it works

| Piece | What it does |
| --- | --- |
| `tap.py` | Six hooks in `~/.claude/settings.json`: `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, `Stop` and `SessionEnd`. Each call appends one line to `feed/<session>.jsonl`, only when the session's directory is inside a watched project, with secrets replaced by `[secret]`. It prints nothing and never blocks; errors are swallowed. |
| `codepanion.events()` | Polled by the automation daemon every 10 s. It turns the feed into signals (`tanka codepanion signals`) with no model, and emits only the ones that wake an active lens of a codepanion that passes `check`, within its hourly budget and outside quiet hours. |
| Tools | `codepanion_sessions`, `codepanion_digest` and `codepanion_diff` read; `codepanion_note` queues a remark. A lens that finds something left pending puts it on a card with the desk's `desk_card`. None of them writes to a watched project. |
| Notes | `notes/<workspace>.jsonl`, shown by `tanka codepanion notes` and counted in the status line segment. A desktop notification is added when `"notify": true` is set. |
| The page | `page.py` and `page.js`: its tabs, its items in the chat, and the tap's and watch's rows in Health. |

## Built in, with no lens

These run from the daemon's poll with no model call, so they cost nothing and work before you build any lens. Their notes are in the persona's language and name (`persona.json`).

- **Guardian** (`"guardian": true`). It speaks once when a session force-pushes (`git push --force`, `-f`, `--force-with-lease`, `+branch`), when a commit or the uncommitted tree adds something shaped like a secret (it names `file:line`, never the value), and when a commit is signed with a name whose earlier commits (5 or more) all used another email. Pause it from the page or with `tanka codepanion lens <workspace> guardian pause`.
- **Session close** (`"session_close": true`). When a session ends, or goes quiet for 3 hours, the files it left uncommitted and the open items of its todo list become one check card for the project, up to 5 items. A session that closed more than 12 hours before the daemon first saw it is marked done with no card.
- **Recap**, optional: `tanka codepanion recap <workspace> on` copies a lens that wakes on `session_closed` and says what changed, what was not verified and what is at risk. It is one model run per session, within the budget, and counts as one of the 3 lenses.

**Feedback that acts.** Two 👎 in a row on one lens make the codepanion propose, once, to wake it less often or pause it; the page shows the proposal with its buttons, and `tanka codepanion lens <workspace> <lens> stricter|pause|resume` does the same. Stricter raises the thresholds of the signals the lens wakes on by half, at least one. The page's health tab shows what the workspace spent today on chat and automation runs (`.tanka/costs.jsonl`).

## Risk

- **The feed holds your prompts and commands** (prompts cut at 500 characters, targets at 120, errors at 300), for the watched projects only. It stays in `~/.tanka/shared/codepanion/`, and it reaches the model only as digests when a lens wakes. Secret scrubbing is pattern-based: a secret in an unusual format can get through. Do not watch projects whose prompts you would not send to the model twice.
- **The tap runs in every Claude Code session** you open, one short Python process per hook. For an unwatched project it reads `watch.json` and exits.
- **`codepanion_diff` reads your repository with git**, with fsmonitor, hooks, attributes, external diff, textconv and configured filters disabled, so a repository's own config cannot run programs through it. Filters listed only in `.git/info/attributes` are the one path left; the attribute source is still forced to an empty tree on git 2.42+.
- **The page (`tanka ui`) shows the feed** to whoever has its URL: it is private by construction (127.0.0.1 only, a token in a header, a strict Content-Security-Policy, text never parsed as HTML); see [`docs/page.md`](../../docs/page.md).
- **The assistant can add and tick cards** (through the desk) from what it reads in a session, and that text can come from a page or a file the session opened. It cannot archive or delete them, they are only shown to you, and their text is shown as text.
- **The backtest** parses Claude Code's transcripts, whose format is internal and may change between versions. It only affects the backtest, never the live feed.

## Scope

A project in `watch.json` belongs to one workspace. You can run several codepanions at once, each with its own persona, lenses and budget, on different projects: one tap and one daemon serve them all, and the page switches between them. Two on the same project are not supported: adding the project to a second workspace moves it. A codepanion's tools only see the sessions of its own projects, and refuse any other session, even one whose id they are given. `watch.json`, `codepanion.json` and the lenses are written by you, never by the assistant: `watch.json` sits outside every workspace, and the other two sit under `.claude/`, which the assistant cannot write.

## Configuration

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_CODEPANION_HOME` | `~/.tanka/shared/codepanion` | `watch.json`, the feed, the notes, feedback and the daemon state |
| `TANKA_CODEPANION_FRESH_SECONDS` | `600` | A signal older than this when first seen is recorded but wakes nobody |
| `TANKA_CODEPANION_RECENT_HOURS` | `24` | How far back `codepanion_sessions` and the daemon look |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Where the tap is installed and where the backtest finds transcripts |

Per codepanion, in `<workspace>/.claude/codepanion.json`:

| Key | Default | Meaning |
| --- | --- | --- |
| `lenses` | `[]` | Active lens names, at most 3, each one `.claude/codepanion/lenses/<name>.md` |
| `proactivity` | `1` | `0` off, `1` whisper (notes and status line) |
| `notify` | `false` | Also send a desktop notification for every note |
| `quiet_hours` | `""` | e.g. `20:00-08:00`: no wake-ups inside it |
| `budget` | `{"runs_per_hour": 6, "notes_per_hour": 3}` | At most 12 and 6 |
| `thresholds` | `{"turn_lines": 30, "turn_files": 3, "stuck": 3, "idle_minutes": 20}` | When each signal fires |
| `guardian` | `true` | The built-in force push, secret and commit identity checks |
| `session_close` | `true` | Cards for what a session leaves uncommitted or unfinished |

## Files

```
modules/codepanion/
├── codepanion.py    the library: tap, signals, lenses and check, tools, backtest
├── tap.py          the hook command
├── cli.py          tanka codepanion …
├── page.py, page.js its part of the page (docs/page.md): Notes, Sessions, Lenses
├── lenses/         lenses the module ships: recap.md
└── skill/          SKILL.md and the four tools
```
