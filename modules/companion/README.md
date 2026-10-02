# Companion

A counterpart that watches your Claude Code sessions, asks the question that makes you explain, remembers what was left pending, and speaks first when one of **your** lenses says so. It never touches code. Design and reasoning: [`docs/companion.md`](../../docs/companion.md).

The module ships the mechanism and no role. You build the role, meaning the lenses, with `/new-companion` in `tanka dev`, and backtest it against your own past sessions before it goes live.

## Setup

```bash
tanka start duck                                  # a workspace for it: its persona, its language
tanka install companion duck                      # the tools, companion.json, an empty lenses folder
tanka companion watch add ~/code/my-project duck  # which projects it may see (repeat per project)
tanka companion tap install                       # observe-only hooks in your normal Claude Code
tanka dev duck                                    # then: /new-companion  (lenses, check, backtest)
tanka trigger add duck watch --on companion:* --budget 0.15 "React to the signal with your lenses."
tanka automation daemon                           # keep it running in a terminal
tanka companion statusline install                # optional: 🦆 N in your status line
```

Read what it said with `tanka companion notes`, and rate a note with `tanka companion rate <id> good|bad`. Or open the page: `tanka companion ui` (`/companion-ui` in `tanka dev`). Its first tab is a **chat with the companion**: you write "recuérdame a las 18:00…" or "tengo pendiente…" and the workspace's assistant makes the cards with its tools. Each answer carries chips for the cards it added or ticked (a click finds the card in **Your day**, the panel beside the chat with the day's reminders and the pending checks by topic). Its lens notes and the reminders that come due appear in the same chat, so it also speaks on its own; a fired reminder stays there with what became of it (due, snoozed, done or archived) and its done and snooze buttons, and a failed answer offers the message again. The page is in English; the assistant answers in the language you write in. The other tabs hold the notes and their 👍/👎, the watched sessions with their timelines and the signals they crossed, the lenses with their check status and ratings, and whether the tap and the daemon are running. `--detach` keeps it in the background; `tanka companion ui stop` closes it. **It also starts the automation daemon** when none is running, so reminders notify; the daemon keeps running after the page closes (`tanka automation stop`), and runs your routines and triggers too. `--no-daemon` skips it.

## How it works

| Piece | What it does |
| --- | --- |
| `tap.py` | Six hooks in `~/.claude/settings.json`: `SessionStart`, `UserPromptSubmit`, `PostToolUse`, `PostToolUseFailure`, `Stop` and `SessionEnd`. Each call appends one line to `feed/<session>.jsonl`, only when the session's directory is inside a watched project, with secrets replaced by `[secret]`. It prints nothing and never blocks; errors are swallowed. |
| `companion.events()` | Polled by the automation daemon every 10 s. It turns the feed into signals (`tanka companion signals`) with no model, and emits only the ones that wake an active lens of a companion that passes `check`, within its hourly budget and outside quiet hours. |
| Tools | `companion_sessions`, `companion_digest`, `companion_diff` and `companion_pending` read; `companion_note` queues a remark; `companion_card` adds or ticks a card. None of them writes to a watched project. |
| Notes | `notes/<workspace>.jsonl`, shown by `tanka companion notes` and counted in the status line segment. A desktop notification is added when `"notify": true` is set. |
| Cards | `cards/<workspace>.json`. A **check** card holds the pending items of one topic: a watched project (by its folder name) or any subject. A **reminder** is one thing at one time (`HH:MM` or `YYYY-MM-DD HH:MM`, at most 60 days ahead). The assistant adds and ticks them with `companion_card` when the user asks, in the page's chat or in `tanka` ("recuérdame a las 17:30…", "tengo pendiente…", "ya lo hice"), or when a lens finds something left pending, with its evidence. It never archives. The page does not create cards; there the user ticks, snoozes and archives them. Lens-found cards share the `notes_per_hour` budget; what the user asks for is not rationed. A due reminder raises one desktop notification from the daemon's poll (no model call) and shows as ⏰ N in the status line until marked done. |

## Risk

- **The feed holds your prompts and commands** (prompts cut at 500 characters, targets at 120, errors at 300), for the watched projects only. It stays in `~/.tanka/shared/companion/`, and it reaches the model only as digests when a lens wakes. Secret scrubbing is pattern-based: a secret in an unusual format can get through. Do not watch projects whose prompts you would not send to the model twice.
- **The tap runs in every Claude Code session** you open, one short Python process per hook. For an unwatched project it reads `watch.json` and exits.
- **`companion_diff` reads your repository with git**, with fsmonitor, hooks, attributes, external diff, textconv and configured filters disabled, so a repository's own config cannot run programs through it. Filters listed only in `.git/info/attributes` are the one path left; the attribute source is still forced to an empty tree on git 2.42+.
- **The page (`tanka companion ui`) shows the feed** to whoever has its URL. It listens on 127.0.0.1 only, refuses any other `Host` header (so a web page cannot reach it through DNS rebinding), and needs the random token in the URL it prints; the API takes the token only in a header, which another origin cannot send without a preflight the server never grants. The token is kept in `ui.json` (mode 600) while it runs. Every text is set as text in a page with a strict Content-Security-Policy, because notes and prompts may carry markup from what the session read. It writes ratings, seen marks, ticks, snoozes and archives, and the chat.
- **The daily brief** comes from the daemon's poll too, with no model: once a day at the workspace's brief time (`tanka companion brief <workspace> [HH:MM|off] [--stale DAYS]`, default 09:00), the chat shows the reminders due today, the open items per topic, and the ones open 3 days or more, with one desktop notification. A day with nothing open says nothing; a machine that wakes more than 12 h after the brief time skips the day. Routine and trigger reports of the workspace (`.tanka/reports.jsonl`) show in the chat too.
- **Reminders need the daemon.** The notification comes from the automation daemon's poll of the companion, which runs with or without a trigger. `tanka companion ui` starts the daemon if none runs, and that daemon also runs every routine and trigger you have, each a model run with its own budget. Without a daemon, a due reminder still shows in the page and the status line, but nothing pops up.
- **Each chat message is a model run** (`tanka run` in the workspace, at most 10 turns and 0.30 USD, one at a time, 40 a day per workspace). The day's messages share one session, resumed with `--resume`, so it remembers what you said earlier today; a new day starts a new one, opened with the earlier days' last 8 messages. Each message also carries what the companion said on its own since your last one (fired reminders, notes, the brief, routine reports, with their ids), so "listo" ticks the right card. The run streams, and the page shows the answer and the tool in use while it is written. The chat is kept in `chat/<workspace>.jsonl`.
- **The assistant can add and tick cards** from what it reads in a session, and that text can come from a page or a file the session opened. It cannot archive or delete them, they are only shown to you, and their text is shown as text.
- **The backtest** parses Claude Code's transcripts, whose format is internal and may change between versions. It only affects the backtest, never the live feed.

## Scope

A project in `watch.json` belongs to one workspace. A companion's tools only see the sessions of its own projects, and refuse any other session, even one whose id they are given. `watch.json`, `companion.json` and the lenses are written by you, never by the assistant: `watch.json` sits outside every workspace, and the other two sit under `.claude/`, which the assistant cannot write.

## Configuration

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_COMPANION_HOME` | `~/.tanka/shared/companion` | `watch.json`, the feed, the notes, feedback and the daemon state |
| `TANKA_COMPANION_FRESH_SECONDS` | `600` | A signal older than this when first seen is recorded but wakes nobody |
| `TANKA_COMPANION_RECENT_HOURS` | `24` | How far back `companion_sessions` and the daemon look |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | Where the tap is installed and where the backtest finds transcripts |

Per companion, in `<workspace>/.claude/companion.json`:

| Key | Default | Meaning |
| --- | --- | --- |
| `lenses` | `[]` | Active lens names, at most 3, each one `.claude/companion/lenses/<name>.md` |
| `proactivity` | `1` | `0` off, `1` whisper (notes and status line) |
| `notify` | `false` | Also send a desktop notification for every note |
| `quiet_hours` | `""` | e.g. `20:00-08:00`: no wake-ups inside it |
| `budget` | `{"runs_per_hour": 6, "notes_per_hour": 3}` | At most 12 and 6 |
| `thresholds` | `{"turn_lines": 30, "turn_files": 3, "stuck": 3, "idle_minutes": 20}` | When each signal fires |

## Files

```
modules/companion/
├── companion.py    the library: tap, signals, lenses and check, tools, backtest
├── tap.py          the hook command
├── cli.py          tanka companion …
├── ui.py, ui.html  tanka companion ui: the local page
├── chat.py         the page's chat: one tanka run per message, one session a day
└── skill/          SKILL.md and the six tools
```
