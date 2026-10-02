# Routines and triggers

Tanka can run the assistant without the user: on a clock (**routines**) or when something arrives (**triggers**). Both start `tanka run` in the workspace, unattended, and tell the user only what needs them.

```bash
tanka routine add personal morning --every 1d "Summarise yesterday's client chats and what is still open."
tanka trigger add personal clients --on whatsapp:client "Read the new message and prepare the reply you would give."
tanka trigger add personal mail --on gmail:* "Read the new email and say whether it needs me today."
tanka automation service install    # keep the daemon running across logins: a launchd agent on macOS, systemd --user on Linux; or: tanka automation daemon [--detach]
tanka automation ensure             # start it if none runs and there is work; `tanka start`, `tanka <ws>` and `tanka dev` do this (TANKA_NO_DAEMON=1 skips it)
tanka automation status | stop      # one daemon at a time: a lock in ~/.tanka/shared/automation/daemon.lock
tanka automation alert personal --whatsapp +15551234567   # also alert on WhatsApp, to the user's own number
tanka automation log personal       # what ran, when, and what it reported
```

`tanka routine|trigger list|remove|run` manage them; `run` fires one now, by hand.

## Where they live

Each workspace keeps its own in `.claude/automations.json`. The assistant cannot write there (its Write allowlist is `.tanka/` and `notes/`), so it cannot schedule itself or widen what an automation may do. The daemon keeps its clock in `~/.tanka/shared/automation/` (`TANKA_AUTOMATION_HOME`), with a lock so a second one exits instead of starting every run twice. It polls a module's events when some trigger listens to them, or always when the module's `module.json` marks its events `"always": true` (the companion, whose poll fires reminders). `tanka companion ui` starts it in the background when none is running.

## Events

Modules declare the events they produce in `module.json` (`"events": {"poll_seconds": …}`) and print them from `cli.py events`, one JSON object per line, with no message text:

| Event | `--on` | Latency |
| --- | --- | --- |
| New WhatsApp message from a contact with a role | `whatsapp:<role>` or `whatsapp:*` | ~3 s (a listener inside the page) |
| New unread Gmail in an assigned mailbox | `gmail:<address>` or `gmail:*` | ~60 s (the mailbox feed) |
| A signal from a watched Claude Code session that wakes a lens | `companion:<signal>` or `companion:*` | ~10 s, after the turn ends |

An event only reaches the workspace whose scope owns it: a message from a contact whose role belongs to another workspace, or with no role, wakes nobody. Messages arriving in a burst fire one run (10 s of quiet, or 60 s at most).

## What an unattended run may do

- **By default it reads and drafts.** The daemon activates an objective that allows the `read` and `draft` classes only, so the harness refuses every send. What needs the user goes to `.tanka/drafts/`.
- **Replying without the user is opt-in per recipient**, set by the user where the assistant cannot write: `"auto_reply": true` on a WhatsApp role in `contacts.json`, or on a mailbox in Gmail's `accounts.json`. Only then does the objective allow `send`, and the send tools check it again themselves: with `TANKA_UNATTENDED=1` (set by `tanka run`) they refuse any recipient not opted in.
- The run ends with `REPORT: <one sentence>`, or `REPORT: -`. A report reaches the user as a desktop notification, a WhatsApp to their own number if configured, and a line in `.tanka/automation.log`; it is also kept in `.tanka/reports.jsonl`, which the companion's chat shows when the workspace has one (the companion's own lens runs excluded: they speak through their notes).
- The objective the user had active is put back after the run.

## Limits

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_AUTOMATION_MAX_RUNS_PER_HOUR` | `20` | Runs across all workspaces per hour; the rest are logged and skipped |
| `TANKA_AUTOMATION_BUDGET_USD` | `0.50` | Spend cap per run (`--budget` on a routine or trigger overrides it) |
| `TANKA_AUTOMATION_HOME` | `~/.tanka/shared/automation` | The daemon's state |

Every run costs a model call, so a trigger on a busy role costs accordingly: start with the roles and mailboxes that matter.
