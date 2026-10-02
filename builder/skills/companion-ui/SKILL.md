---
name: companion-ui
description: Open the companion's local page in the browser — a chat with the companion that makes the check cards and reminders and speaks there on its own, its notes with 👍/👎, the watched sessions with their timelines and signals, the lenses with their check status, and whether the tap and the daemon are running. Use when the user says "abre la UI del companion", "/companion-ui", "muéstrame el companion", "qué me ha dicho el patito", or wants to rate notes without the terminal.
argument-hint: "[stop]"
---

# Companion UI

Run from the repository:

- `bin/tanka companion ui --detach`: starts the page in the background, or reuses the one already running, and opens it in the browser. It prints the URL; give it to the user as is, since it carries the token that opens the page.
- It also starts the automation daemon when none runs, so reminders notify, and prints a line saying so: pass that line on, since the daemon also runs the user's routines and triggers and keeps running after the page closes (`bin/tanka automation stop`).
- `bin/tanka companion ui stop` when the user asks to close it (argument `stop`). That leaves the daemon running.

If it prints `x The UI did not start`, read the log it names and say what failed.

The page's first tab is a chat: each message is a `tanka run` in the workspace (at most 0.30 USD, 40 a day), and the assistant makes the cards there with its tools, shown as chips on its answer and in the "Your day" panel; the user only ticks, snoozes and archives them. The page is in English; the assistant replies in the user's language. The companion also speaks there on its own: fired reminders, lens notes, a daily brief (`tanka companion brief <workspace> HH:MM|off`, default 09:00) and the workspace's routine reports. The page also records ratings and seen marks. Lenses, thresholds and budget are not edited there: that is `/new-companion`, which checks and backtests every change. If the page shows the tap or the daemon as not running, tell the user the command it shows; they are theirs to run.
