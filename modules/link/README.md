# Link

Your open Claude Code sessions, seen from Tanka. The **tanka-link** plugin (in `link/` at the root of this
repository) registers every Claude Code session you open; this module lists them on the page (**Sessions** tab)
and lets you send a prompt into one. The assistant can do it too, with `link_send`, always after showing you the
exact text and waiting for your yes.

## Setup

```bash
claude plugin marketplace add ~/my/tanka        # this repository is a plugin marketplace
claude plugin install tanka-link@tanka          # every new Claude Code session registers itself
tanka install link <workspace>                  # link_sessions, link_send and the Sessions tab
```

Restart the Claude Code sessions already open: plugins and hooks reach new sessions only.

## How it works

- **The listener.** `SessionStart` and `Stop` start `link/listen.py` in the background as an `asyncRewake` hook.
  It writes the session (id, folder, project, branch) to `~/.tanka/shared/link/sessions/<id>.json`, keeps a
  heartbeat, and waits for a file in `inbox/<id>/`. When one arrives it moves it to `delivered/<id>/`, prints it
  and exits with code 2: Claude Code hands it to the session at once, even while it waits for you. The next
  `Stop` starts a new listener. `SessionEnd` unregisters the session.
- **Sending.** One JSON file per message in the session's inbox. A session that is working gets it when its turn
  ends. The message tells the session who sent it (you from the page, or your assistant with your approval).
- **What a session does with it** is decided by that session's own permissions, exactly as if you had typed it.
  Nothing opens a network port; the files are yours.

## Limits

- A hook has a timeout (24 h here). A session that stays idle longer stops listening until its next turn.
- `asyncRewake` is documented for waking a session; using it as a long-lived listener is this module's own
  pattern, tested on Claude Code 2.1.295.
- You do not see the session's answer on the page: look at the session.
