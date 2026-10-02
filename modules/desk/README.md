# Desk

Your day, kept by your assistant: reminders at a time, pending checks per topic, and a daily brief. You see them in **Your day**, beside the chat on the page (`tanka ui`), where you tick, snooze and archive them. The assistant adds and ticks; it never deletes.

## Setup

```bash
tanka install desk <workspace>          # desk_card and desk_pending, and .claude/desk.json
tanka automation service install       # reminders notify from the automation daemon's poll
tanka ui <workspace>                   # the page
```

The codepanion needs the desk (it puts what a session leaves open on your cards), so `tanka install codepanion` installs it first.

## How it works

| Piece | What it does |
| --- | --- |
| Cards | `cards/<workspace>.json`. A **check** card holds the pending items of one topic: a project the codepanion watches (by its folder name) or any subject. A **reminder** is one thing at one time (`HH:MM` or `YYYY-MM-DD HH:MM`, at most 60 days ahead). |
| Tools | `desk_pending` lists what is open; `desk_card` adds a check item or a reminder, or ticks one. Adding the same open thing twice returns the existing one. |
| The poll | `cli.py events`, run by the automation daemon every 10 s with no model: a reminder that comes due raises one desktop notification and shows in the chat with done and snooze buttons; once a day at the brief time the chat shows what the day holds and what has stalled. |
| The page | `page.py` and `page.js` (see [`docs/page.md`](../../docs/page.md)): Your day, the fired reminders and the brief in the chat, and chips under an answer for the cards it added or ticked. |

What the user asks for is not rationed. A card the assistant adds on its own, with evidence (a lens that found something left pending), counts against `found_per_hour`.

## Configuration

`.claude/desk.json`, which the assistant cannot write:

| Key | Default | Meaning |
| --- | --- | --- |
| `brief` | `"09:00"` | When the daily brief shows in the chat; `""` turns it off. `tanka desk brief <workspace> HH:MM\|off` sets it |
| `stale_days` | `3` | An open item this old is called stalled in the brief (`--stale DAYS`) |
| `found_per_hour` | `3` | Cards the assistant may add on its own per hour |

A day with nothing open has no brief, and a machine that wakes more than 12 h after the brief time skips that day.

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_DESK_HOME` | `~/.tanka/shared/desk` | The cards and the brief's state, outside every workspace |

## Moving in from an older codepanion

Cards, the chat and the brief settings used to live in the codepanion module. The daemon's poll moves the data the first time it runs (`shared/codepanion/cards` to `shared/desk/cards`, `shared/codepanion/chat` to `shared/page/chat`); nothing is moved over existing data. Run `tanka desk migrate` once to finish: it also moves `brief`/`stale_days` from `codepanion.json` to `desk.json`, drops the card tools an older codepanion carried, and installs the desk in its workspace. Until then the page's Health says which codepanion is missing its desk. The poll never writes `desk.json` itself, so deleting it turns the desk's reminders and brief off for good.

## Files

| File | What it does |
| --- | --- |
| `desk.py` | Cards, reminders, the brief and the move-in |
| `cli.py` | `tanka desk pending\|brief\|migrate`, the daemon's `events`, and `post-install` |
| `page.py`, `page.js` | Its part of the page |
| `skill/` | `SKILL.md` and the two tools |

Tests: `tests/test_desk.py`.
