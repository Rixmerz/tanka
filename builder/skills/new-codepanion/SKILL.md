---
name: new-codepanion
description: Build or reshape a codepanion — the counterpart that watches the user's Claude Code sessions and speaks first — for the Tanka workspace in TANKA_WORKSPACE: its lenses, signals, thresholds and budget, checked and backtested against the user's own past sessions. Use when the user says "arma mi codepanion", "build my codepanion", "quiero que me revise / me recuerde / me pregunte", wants to change what their codepanion says or how often, or complains it talks too much or too little.
argument-hint: "[what you want it to be for you]"
---

# New codepanion

The module ships no role. You build one for this person, from what they tell you and from what their own sessions show. A codepanion that talks too much gets muted, so every step below is about saying less, and saying it well. Read `docs/codepanion.md` §2 and `modules/codepanion/README.md` first. The examples in `examples/` show what a good lens looks like: start from one when it fits, and never install one unchanged.

The workspace is `$TANKA_WORKSPACE`; its name is the directory's name. All commands run from the repository: `bin/tanka codepanion …`.

## 0. Is it ready?

- `.claude/codepanion.json` must exist in the workspace. If it does not: `bin/tanka install codepanion <name>`.
- `bin/tanka codepanion watch list` must show at least one project for this workspace. If not, ask which project folders it should watch, then `bin/tanka codepanion watch add <path> <name>`.
- `bin/tanka codepanion signals` lists what lenses can wake on. A lens can use nothing else.

## 1. Ask, one question at a time

1. What do they want it to be for them: someone who asks, who reviews, who reminds, who warns? Get two real sentences it should say.
2. What do they forget, or get stuck on, when they code with Claude?
3. What would make them mute it? Keep the answer: it becomes "Never like this" examples.
4. Quiet hours, and whether they want a desktop notification per note (`notify`).

## 2. Look at their history, if they agree

Ask first: this reads their past sessions in the watched projects.

`bin/tanka codepanion history <name> --since 14d` shows how often each signal would have fired, with the last examples. Build a lens only for something that actually shows up there, and tell them which sessions it came from. If a habit they named never appears, say so, and do not build a lens for it.

If they have left feedback before (`~/.tanka/shared/codepanion/feedback.jsonl`, the lines with `"scope": "<name>"`), read it: a lens with 👎 needs a stricter rubric, a higher threshold, or removal.

## 3. Write

- At most **3** lenses in `.claude/codepanion/lenses/<name>.md`, in the format of `examples/`: frontmatter `name`, `wakes_on`, `speaks` (`question`, `finding` or `reminder`), `severity: low`; then `## Rubric` with numbered criteria that can say no, `## Say it like this` with at least one example, and `## Never like this` with at least two.
- Write the examples in the user's language: the codepanion speaks like them.
- A `question` lens has only questions in its examples, and never an answer.
- A `reminder` lens may record what it finds as a check item on the project's card (`desk_card`, with the digest line as evidence) instead of a note; say so in its "Say it like this".
- `.claude/codepanion.json`: `lenses` (the active names), `thresholds` (from what the history showed: raise a threshold that would fire several times a day), `budget`, `quiet_hours`, `notify`, and `proactivity: 1`.

## 4. Check

`bin/tanka codepanion check <name>` → ok. Fix every problem it names; do not weaken a rule to pass it.

## 5. Backtest

1. `bin/tanka codepanion backtest <name> --since 14d` lists every wake-up the lenses would have had, and when. More than about 5 a day is too many: tighten the thresholds or the rubric, and run it again.
2. With their OK, since each one is a model call (about 0.15 USD at most), `--say 3` runs the codepanion on three of them and prints what it would have said. Show them each remark and ask 👍 or 👎. For each 👎, find the criterion that should have said no, and sharpen it.
3. Repeat until they would put up with every remark and the count per day.

Nothing is sent during a backtest.

## 6. Go live, quietly

If it is not running yet, tell them the command that is theirs to run, and why: `bin/tanka codepanion setup <name> <project path>`. It installs the tap (observe-only hooks in their normal Claude Code), adds the `watch` trigger, keeps the automation daemon running as a login service, and ends with a self-test that sends nothing. Claude Code sessions already open in the project must be restarted to be watched.

Then `bin/tanka ui <name>`: the codepanion's notes show in the chat and in the Notes tab, where 👍/👎 tunes it.
