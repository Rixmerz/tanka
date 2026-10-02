# Codepanion — a counterpart that watches your coding sessions and speaks first

Status: **phase 1 built** (`modules/codepanion/`, `builder/skills/new-codepanion/`, `tests/test_codepanion.py`). Phases 2 and 3 below are still design.

## 1. The idea

*The Pragmatic Programmer* calls it rubber ducking: explain the problem to a duck on your desk, and saying it out loud shows you the bug. The duck works because it listens. It is also the duck's limit: it only listens, and only when you talk to it.

The codepanion is that duck taken further. It **watches** your Claude Code sessions as they happen, **asks** the question that makes you explain, **remembers** what was left pending, and **speaks first** when something matters, without waiting for a prompt. It is a counterpart, not a copilot: it never writes code, never runs a command in your project and never acts in your coding session. Its only output is what it says to you.

Two sentences it should be able to say, unprompted:

- "The last three test runs failed with the same `KeyError`, and the last two edits were retries. What do you think the key is when it fails?"
- "You're about to push to a personal repository, but the commits are signed with your work email."

### What it is not

| Not | Because |
|---|---|
| A second coding agent | The session you are in already codes. Two agents editing the same tree is a conflict, not help. |
| A gatekeeper | It never blocks, denies or slows down your coding session. It advises, you decide. |
| A chatbot you have to drive | Chatting is available, but silence from you does not mean silence from it. |
| Clippy | A proactive assistant that talks too much gets muted and then uninstalled. Holding back is a feature (§6.2). |

## 2. Everyone builds their own; Tanka ships the mechanism

A good codepanion depends on who it is watching: what you forget, what you get stuck on, what would annoy you. A preset guesses all three for everyone and gets them wrong for most people. So the module ships **no role**. It ships the machinery that every codepanion needs and nobody should rebuild, plus a builder skill, `new-codepanion` (§7), that knows how to turn one person's habits into a codepanion that works.

| Fixed, shipped by the module | Yours, built with `new-codepanion` |
|---|---|
| The tap, the feed, `watch.json` scope, secret scrubbing | Which projects it watches |
| The signal engine and its catalog (§4) | Which signals wake it, and at what thresholds |
| The six tools and the "cannot touch code" guarantees (§5) | Its lenses: what it looks for, how it judges, what it says |
| Channels, etiquette, budgets (§6) | Which channels, how proactive, quiet hours, how much it may spend |
| `tanka codepanion check` and `backtest` | Its name, voice and language (`persona.json`) |

The fixed half is what makes any codepanion safe and quiet enough to live with. The other half is what makes it worth having.

### 2.1 A lens

A **lens** is one thing your codepanion looks out for. It is a file, `.claude/codepanion/lenses/<name>.md`, with a rubric and the signals that wake it, because Haiku classifies well against an explicit rubric and judges badly without one (architecture §2.3):

```markdown
---
name: stuck
wakes_on: [stuck]                 # signals from the catalog, or custom ones (§4.1)
speaks: question                  # question | finding | reminder
severity: low                     # the highest it may use; high allows a nudge
---
## Rubric
Speak only when every one of these holds:
1. The same failure has repeated, and the last edits were retries, not a new approach.
2. Nothing in the last prompt states a hypothesis.
Otherwise say nothing.

## Say it like this
- "The last three runs failed with the same KeyError. What do you think the key is when it fails?"

## Never like this
- "You should add a try/except."   (an answer, and this lens only asks)
- "Tests are failing."             (the user can see that; no evidence, no question)
```

### 2.2 Examples, not presets

The skill carries worked examples as references, never installed on their own. It reads them to know what a good lens looks like, then writes yours:

| Example | Looks for | Speaks |
|---|---|---|
| duck | Retries without a hypothesis, a fix that contradicts what you said earlier | Questions only |
| reviewer | A turn that changed code meaningfully (uses the Tier 2 reviewer) | Findings with confidence |
| keeper | Todos never closed, "we'll do it later", uncommitted work | Reminders |
| guardian | Secrets in a diff, force-push to `main`, a commit identity that doesn't match the remote | Findings, may nudge |
| pacer | The same failure 3 times, 3 hours without a commit, drift from the session's goal | Questions or reminders |

### 2.3 Rules a lens must pass

These are what "works" means. `tanka codepanion check` enforces them, and the skill does not finish until they pass:

- **[checked]** At most **3 active lenses** per codepanion. Every extra one competes for the same attention.
- **[checked]** `wakes_on` names at least one signal, so no lens runs on every event.
- **[checked]** The rubric has explicit, numbered criteria that can say no, plus at least one "say it like this" and two "never like this" examples.
- **[checked]** `speaks` is declared, and a `question` lens has no answer in its examples.
- **[checked]** `severity: high` (which allows a nudge) is used only by lenses whose signals are deterministic.
- **[checked]** The budget fits the module's limits (§4).
- Every note must cite its evidence: an event, a file, a command. A note with nothing to point at is not sent.
- **Backtested** (§7): it has run in dry-run against the user's own past sessions, and the user has accepted what it would have said.

## 3. How it sees: the tap

The codepanion runs in its own Tanka workspace. What it watches is your **normal** Claude Code, which Tanka does not control. A small observe-only plugin, the **tap**, sits in your normal Claude Code and writes a compact event feed that the codepanion reads.

```
your Claude Code session                    codepanion (Tanka workspace, Haiku)
  └─ tap plugin (hooks, observe-only)          ├─ tanka automation daemon polls `tanka codepanion events`
       └─ appends to                            ├─ deterministic signals → trigger → tanka run
          ~/.tanka/shared/codepanion/feed/       ├─ tools: sessions, digest, diff, review, note, tasks
          <session_id>.jsonl                    └─ speaks through the channels in §6
```

### 3.1 Why hooks and not the transcript

Claude Code already writes every session to `~/.claude/projects/<key>/<session>.jsonl`, but that format is internal. One real transcript has 15 record types and more than 100 distinct fields, and it changes between versions. Hook input is documented and stable, and every hook call includes `transcript_path`, so a lens that needs more detail can still read the transcript. The feed stays small, and the parsing stays in one place.

### 3.2 Events the tap writes

| Hook | Event | Fields (truncated, secrets scrubbed) |
|---|---|---|
| `SessionStart` | `session_start` | cwd, git branch, remote, source (startup/resume/compact) |
| `UserPromptSubmit` | `prompt` | first 500 characters of the prompt |
| `PostToolUse` / `PostToolUseFailure` | `tool` | tool name, target (file path or the first 120 characters of the command), ok/fail, error head |
| `Stop` | `turn_end` | files touched this turn, lines +/−, tool count, failures |
| `SessionEnd` | `session_end` | duration, still-open todos |

### 3.3 Tap rules

- **Observe only.** It never returns a permission decision and never blocks. It adds context only in relay mode (§6.1), which is opt-in.
- **Fast and silent.** It appends one line and exits within 50 ms. Any error is swallowed: a broken tap must never break your session.
- **Scoped at the source.** `~/.tanka/shared/codepanion/watch.json` lists the project paths each codepanion workspace may see. Events from other paths are dropped *before* they are written. The default watches nothing, so you opt in per project. The assistant cannot write that file (module-rules §3).
- **Scrubbed at the source.** Secret patterns (reused from `tanka_common.secret_patterns`) are replaced with `[secret]` before writing.
- `tanka codepanion tap install` adds six hook entries to `~/.claude/settings.json`, each one running `modules/codepanion/tap.py` from this repository, after backing the file up; `tap remove` takes out exactly those entries. It is not a plugin: an installed plugin is copied into Claude Code's cache, away from the library and the scrubbing patterns it shares with Tanka, and `git pull` would no longer update it. Measured cost: 30–40 ms per hook.

## 4. When it thinks: three tiers

Every model call costs money and attention, so most of the thinking is free.

**Tier 0: deterministic signals, no model.** `tanka codepanion events` (the module's `cli.py events`, polled by the daemon) reads the feed and emits a signal only when a threshold is crossed:

| Signal | Default threshold | Lenses woken |
|---|---|---|
| `turn_end_substantial` | ≥ 30 lines changed or ≥ 3 files in one turn | reviewer, guardian |
| `stuck` | the same tool failing with the same error head 3 times in a session | duck, pacer |
| `idle_dirty` | 20 min without events and a dirty working tree | keeper |
| `long_uncommitted` | 3 h since the last commit on a session with changes | pacer, keeper |
| `risky_command` | `push --force`, `reset --hard`, `rm -rf`, `DROP`, a prod hostname | guardian |
| `identity_mismatch` | the commit author's email domain differs from the one set for this remote | guardian |
| `session_start` | a new or resumed session in a watched project | keeper (the recap) |
| `session_end` | a session closed with open todos | keeper |

Signals are debounced per session. At most one run per session per `turn_end`, and none while a turn is still streaming. The thresholds are defaults; each codepanion sets its own in `codepanion.json`.

**As shipped.** `turn_end_substantial`, `stuck`, `idle_dirty` and `session_closed` (a session that ended, or went quiet for 3 hours) are signals lenses wake on. The guardian rows (`risky_command` for force pushes, `identity_mismatch` for a commit name whose earlier commits used another email, plus secrets in a commit or the working tree) and the close of a session (uncommitted files and open todos as a check card) do not wake a lens: they are built in and run in tier 0 with no model, so a codepanion with no lens already does them. Two 👎 in a row on a lens make it propose to wake less often or pause. See the module's [README](../modules/codepanion/README.md#built-in-with-no-lens).

### 4.1 Custom signals, without code

When the catalog does not have what a lens needs, the builder skill can declare a **custom signal** in `codepanion.json`: a pattern over feed events, a count and a time window. It cannot contain a script, so a signal never executes anything:

```json
"signals": {
  "prod_touch": { "event": "tool", "match": { "target": "(?i)prod|production" }, "count": 1, "window": "1h" },
  "no_tests":   { "event": "turn_end", "match": { "files": "\\.(py|ts)$" }, "absent": { "event": "tool", "target": "(?i)test" }, "count": 3, "window": "2h" }
}
```

**Tier 1: Haiku triage.** The trigger starts `tanka run` with the signal and a digest (`codepanion_digest`, 6 000 characters maximum). Haiku decides between saying nothing, whispering and nudging, and ends with `REPORT:` like every automation. `guardian` signals that are fully deterministic (`risky_command`, `identity_mismatch`) can skip the model and go straight to a note.

**Tier 2: a stronger reviewer, when it pays.** `codepanion_review` is a subagent tool (Sonnet or Opus, read-only, with a dollar cap; see subagent-rules). It reads the diff against the active lenses' rubrics and returns findings with a confidence rating. Haiku calls it only after `turn_end_substantial`, or when you ask for a review. Haiku triages; it does not judge code quality.

**Budgets**, set in `.claude/codepanion.json`: runs per hour (default 6), notes per hour (default 3), dollars per day (default 1.00). A skipped run is logged, never queued forever.

## 5. The module: four tools, read and draft only

| Tool | Effect | What it does |
|---|---|---|
| `codepanion_sessions` | read | Watched sessions right now: project, branch, duration, last activity, open todos |
| `codepanion_digest` | read | One session's activity since a given point: prompts, tools, failures, files, capped and scrubbed |
| `codepanion_diff` | read | `git status` / `diff` / `log` / `show` of a watched project; refuses any path outside `watch.json` |
| `codepanion_note` | draft | Queues one remark: lens, severity, evidence, the question or finding. **The harness picks the channel, not the model.** |

Cards (reminders and pending checks) moved to the [desk](../modules/desk/README.md), which the codepanion needs: a lens that finds something left pending calls `desk_card`. With the desk's two tools a codepanion workspace has six; Phase 2's `codepanion_review` (a Tier 2 subagent) would most likely fold `codepanion_diff` into it.

There is no `send`, `modify` or write tool that reaches your project.

### 5.1 Why it cannot touch code (structure, not prompt)

- The codepanion lives in its own workspace. Tanka already denies Bash, and Write is limited to `.tanka/` and `notes/`.
- `codepanion_diff` is the only tool that reaches your repository. It runs `git` with an argv allowlist and with `-c core.fsmonitor= -c core.hooksPath=/dev/null --no-ext-diff --no-textconv`. A repository's own config can make `git status` or `git diff` execute programs (fsmonitor, external diff, textconv), and a read tool must not run the repository's code.
- The tap cannot change what your coding agent does, except through relay mode, which is off by default.

## 6. How it speaks

### 6.1 Channels, by urgency

| Channel | When | How |
|---|---|---|
| **whisper** (default) | Everything | A segment in your Claude Code status line (`🦆 2`) plus the note queue. It *adds* a segment, because you probably have a status line already; `tanka codepanion statusline` prints the segment for your own command to include. The queue can also be read and rated in the local page, `tanka ui` ([page](page.md)). |
| **nudge** | `guardian` findings and severity high | A desktop notification: `osascript` on macOS, `notify-send` on Linux |
| **talk** | Whenever you want | `tanka codepanion`, or `tanka <its workspace>`, opens a chat with it in another terminal or pane: the queue, the digest, duck mode. This is the reactive half. |
| **relay** (opt-in, off) | You choose which lenses | On your next prompt the tap adds the pending note to your coding agent's context, labelled as advice, not instructions |

**Relay is off by default, and the reason is prompt injection.** The codepanion reads untrusted text: web pages, tool output and files your agent opened. If it relays that text into an agent that *can* write code and run commands, it becomes an injection channel with a trustworthy-looking label. So relay only carries notes you have already seen in the queue, only from the lenses you allow, and never quotes raw content.

### 6.2 Etiquette (anti-Clippy)

- **Never mid-turn.** Notes are held while the agent is streaming and delivered at `turn_end` or idle.
- **Never twice.** Each note's claim is hashed, and the same claim about the same file is not repeated.
- **Quiet hours** and `tanka codepanion mute <lens> 1h`.
- **Feedback.** Each note can be marked useful or not (`tanka codepanion 👍|👎 <id>`, or in the chat). The feedback is stored where only you can write. Three 👎 on one lens halve its frequency, and the codepanion says it did.
- **Proactivity:** 0 off, 1 whisper. Phase 2 adds 2 (whisper + nudges).

## 7. Configuring it from `tanka dev`

A codepanion is an ordinary Tanka workspace with the module installed. Everything that makes it *yours* is built in `tanka dev`:

```bash
tanka start duck                    # its persona: name, tone, personality, language (persona.json)
tanka install codepanion duck        # the mechanism and the six tools; no lenses yet
tanka codepanion tap install         # the tap, in your normal Claude Code
tanka dev duck                      # /new-codepanion: build its lenses, signals and settings
tanka trigger add duck watch --on codepanion:* "React to the signal with your lenses."
```

### 7.1 The `new-codepanion` skill

It sits next to `new-skill`, `new-tool` and `new-module` in `builder/`, and runs on your default model, not on Haiku. What it knows is not a role. It knows how to build one that works:

1. **Ask**, one question at a time: what you want it to be for you, what you tend to forget, what you get stuck on, what would make you mute it.
2. **Look at your own history**, if you agree. `tanka codepanion history <workspace>` reads your past Claude Code sessions in the watched projects and finds what actually repeats: the same failures, work left uncommitted, force-pushes, long stretches without a test. It proposes lenses grounded in that evidence, each one citing the sessions it came from, and never builds a lens for a habit it cannot show.
3. **Write** at most 3 lenses (§2.1), any custom signals (§4.1) and `codepanion.json`, starting from the examples in §2.2 when one fits.
4. **Check**: `tanka codepanion check` → ok (§2.3).
5. **Backtest**: `tanka codepanion backtest --since 14d` replays your past sessions through the signals and the lenses in dry-run, and shows what it would have said and when. You mark each remark 👍 or 👎; the skill tightens rubrics and thresholds until the 👎 are gone and the count per day is one you would put up with. Nothing is sent during a backtest.
6. **Go live quietly**: proactivity 1 (whisper only) for the first week, then look at the feedback together and raise it if you want.

Running `/new-codepanion` again on an existing codepanion changes it: it reads the feedback left on real notes and proposes what to tighten, drop or add.

### 7.2 Where it lives

The settings live in `.claude/codepanion.json`, and the lenses in `.claude/codepanion/lenses/`. Like `automations.json`, both are out of the assistant's reach: the codepanion cannot rewrite its own rubric or widen its own scope.

```json
{
  "lenses": ["stuck", "unfinished", "big-turn"],
  "proactivity": 1,
  "notify": false,
  "quiet_hours": "20:00-08:00",
  "budget": { "runs_per_hour": 6, "notes_per_hour": 3 },
  "thresholds": { "turn_lines": 30, "turn_files": 3, "stuck": 3, "idle_minutes": 20 }
}
```

Persona is `/tanka:setup`, as in any workspace. The role is kept out of the persona because the assistant can write `persona.json`.

One codepanion per domain, like workspaces: one watches work repositories, another personal ones, each with its own `watch.json` entries, lenses and budget.

## 8. What the current code needs first

Found while reading `plugin/scripts/tanka_automation.py`:

| Gap | Where | Fix |
|---|---|---|
| Alerts reach nobody on macOS | `alert()` only called `notify-send` | **Done:** `tanka_common.desktop_notify` also uses `osascript` |
| No service on macOS | `service()` was systemd only | **Done:** `tanka automation service install` writes a launchd agent on macOS and a systemd unit on Linux; `tanka start`, `tanka <ws>` and `tanka dev` also start the daemon when none runs |
| The daemon only knew two sources | `Daemon.route()` hardcoded the `whatsapp`/`gmail` match and message text | **Done:** an event may carry its own `match` and `what` |
| One global run budget | `TANKA_AUTOMATION_MAX_RUNS_PER_HOUR` shared by all | Per-module budgets, so the codepanion cannot starve WhatsApp triggers or the other way round |
| Debounce tuned for message bursts | `DEBOUNCE_SECONDS`, the 60 s cap | Codepanion signals are already turn-level; the module can mark its events as not needing debounce |

## 9. Plan

**Phase 1 (built): the mechanism, and a first codepanion you build.** The tap, the feed, `watch.json`, three catalog signals (`turn_end_substantial`, `stuck`, `idle_dirty`), the lens format, `check`, `history` and `backtest` (with `--say N` for real model calls in a sandbox), six tools (`sessions`, `digest`, `diff`, `note`, `pending`, `card`), notes with the status line segment and an optional desktop notification (macOS and Linux), `rate`, `new-codepanion` with all six steps, and a local page (since moved into Tanka as `tanka ui`, [page](page.md), with the cards in the [desk](../modules/desk/README.md)) whose first tab is a chat with the workspace's assistant, which makes the cards (check cards per topic, reminders) with its own tools and shows them as chips on its answer, beside a "Your day" panel with the day's reminders and pending checks, while the lens notes, fired reminders (with what became of each), a daily brief with what has stalled (no model, `tanka codepanion brief`) and the workspace's routine reports show in the same stream; each message carries what it said on its own since the user's last one, a new day's session opens with the end of the earlier chat, and answers stream into the page; the page is in English, plus the notes and their ratings, the sessions' timelines, the lenses and the health of the tap and the daemon; it ticks, snoozes and archives cards and records ratings, and never creates cards or edits lenses. Due reminders raise a desktop notification from the daemon's poll and ⏰ N in the status line. The daemon's routing is generic (§8), and its alerts now reach macOS. `tests/test_codepanion.py` covers:

- a session in a watched project produces a feed, one in an unwatched project (or a sibling with the same prefix) produces nothing;
- a secret in a prompt or a command never reaches the feed;
- `check` rejects a lens without `wakes_on`, with fewer than two "never like this" examples, a question lens whose examples are not questions, a rubric without numbered criteria, and a fourth active lens;
- 3 identical failures produce exactly one wake-up, carrying no prompt text; signals older than 10 minutes when first seen wake nobody;
- `codepanion_diff` shows the change without running an external diff, a textconv or a filter configured in the repository;
- `backtest` lists every wake-up and sends nothing, and `--say` runs the model with the feed and notes in a temporary home;
- the page refuses a foreign `Host`, a missing or wrong token, and a cross-origin preflight; it ships a nonce-only CSP and no HTML sinks; and a rating from it reaches `feedback.jsonl`; it ticks and archives cards but has no endpoint to create one;
- the chat runs one message at a time, starts the day's session with `--session-id` and resumes it after, starts again when the session is gone, reports a failed run, refuses empty, long, tokenless and foreign-workspace messages, shows lens notes and fired reminders in the same stream, each fired reminder with what became of it, each answer with the cards it added or ticked, and each failed answer with the message to send again; a reply carries what the codepanion said on its own since the last one, a new day's session the end of the earlier chat; routine reports show, lens runs' do not; the brief comes once at its time, calls out stalled items, says nothing on an empty day, off or too late, and shows its items as they stand; `brief` sets the time and `check` refuses a bad one; the streamed run gives the answer from its result and a draft that follows the text;
- check items group by topic and never duplicate; reminders parse `HH:MM` and dates, refuse the past and more than 60 days ahead; a due reminder notifies once, snooze re-arms it; lens-found cards are rationed and requested ones are not; cards stay in their workspace;
- the module passes `tanka modules check` and installs with its scope.

Still to measure by using it: a week of real notes per day, the 👍 rate and the spend.

**Phase 2: stronger judgment.** `codepanion_review` (subagent), the guardian signals (`risky_command`, `identity_mismatch`), custom signals (§4.1), nudges, a launchd service for macOS, per-module run budgets.

**Phase 3: further.** Opt-in relay, and the talk channel inside the session if pane mods prove stable.

## 10. Risks

- **Noise is the failure mode that kills it.** Measure the 👍 rate from week one. Under 50 % means the thresholds go up before anything else is built.
- **Privacy.** The feed holds your prompts and commands. It stays local under `~/.tanka/shared/codepanion/`, reaches the model only as capped, scrubbed digests, and only for projects you opted in. It is the same provider your coding session already sends it to, but a second copy is still a second copy.
- **Format drift.** Hook payloads are documented, but the transcript fallback is not. Pin a tested Claude Code version in `module.json` and have `tanka doctor` warn above it.
- **Haiku is a weak code judge.** That is why review goes to Tier 2 and Haiku only triages.
- **The tap is the first Tanka piece outside isolation**, in the user's normal Claude Code. Keep it hooks-only, audited and small.

## 11. Open questions

1. Relay: keep it off permanently, or offer it in Phase 3 as designed?
2. Should the talk channel live in a separate terminal (`tanka codepanion`), or, if Claude Code's pane mods prove stable, inside the coding session itself?
3. Name: the module is `codepanion`, and the persona gets the name the user picks.
