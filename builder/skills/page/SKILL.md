---
name: page
description: Open Tanka's local page in the browser — a chat with each workspace's assistant, Your day (reminders and pending checks) when the desk is installed, boards when the workspace has views, the codepanion's notes, sessions and lenses when it has one, and whether the daemon runs. Use when the user says "abre la UI", "abre la página", "/page", "muéstrame el codepanion", "qué me ha dicho el patito", wants to see a board, or wants to rate notes without the terminal.
argument-hint: "[workspace|stop]"
---

# The page

Run from the repository:

- `bin/tanka ui <workspace> --detach`: starts the page in the background, or reuses the one already running, and opens it on that workspace. It prints the URL; give it to the user as is, since it carries the token that opens the page. Without a workspace it opens on the last one used.
- It also starts the automation daemon when none runs (or restarts one running older code), so reminders notify, and prints a line saying so: pass that line on, since the daemon also runs the user's routines and triggers and keeps running after the page closes (`bin/tanka automation stop`).
- `bin/tanka ui stop` when the user asks to close it (argument `stop`). That leaves the daemon running.

If it prints `x The UI did not start`, read the log it names and say what failed.

The page's first tab is a chat: each message is a `tanka run` in the workspace (at most 0.30 USD, 40 a day), and the assistant acts with its own tools. What the installed modules add (docs/page.md): the desk puts Your day beside the chat and the reminders and daily brief in it; boards add a tab per declared view; the codepanion adds its notes, sessions and lenses. The page is in English; the assistant replies in the user's language. Lenses, views and thresholds are not edited there: that is `/new-codepanion` and `/new-view`. If the page shows the daemon or the tap as not running, tell the user the command it shows; they are theirs to run.
