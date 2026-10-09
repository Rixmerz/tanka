# cauce

Your coding tasks on the page. [cauce](https://github.com/Rixmerz/cauce) routes
coding work to one-shot Claude Code workers, escalates on evidence and keeps
every attempt; this module shows its board in the **Code** tab of the page
(`tanka ui`) and gives the assistant tools to read it, to queue new tasks, and
to see and steer your Claude Code sessions that work with cauce, in the
repositories you allow for that workspace.

## Setup

```bash
claude plugin marketplace add Rixmerz/claude-plugins   # cauce itself, if it is not installed yet
claude plugin install cauce@rixmerz                   # 0.6.21 or newer
tanka install cauce <workspace>                       # cauce_board, cauce_task, cauce_memory, cauce_queue, cauce_sessions, cauce_send
tanka cauce allow <workspace> ~/code/webapp           # a repository this workspace may use
tanka ui <workspace>                                  # the page, Code tab
claude plugin install tanka-link@tanka                # optional: send your sessions prompts (see modules/link)
tanka trigger add <workspace> code-endings --on cauce:* "Tell me what ended in my code and what it needs from me"
```

The last line is optional: the automation daemon then runs the assistant each time a cauce task ends in one
of the workspace's repositories (`cauce:<name>` for one repository only).

## How it works

| Piece | What it does |
| --- | --- |
| The program | Every read and write goes through the `cauce` program and the JSON it prints (`board --full --repo`, `show --json`, `queue add --json`). The module never opens cauce's database. It finds the program at `TANKA_CAUCE_BIN`, then on `PATH`, then in the newest Claude Code plugin install. |
| Scope | `repos.json` lists, per workspace, the repositories it may see and queue in, by a short name. A task in any other repository is refused before cauce runs, and the board asks cauce only for the allowed ones. No tool writes that file. |
| Tools | `cauce_board` (read): what needs you, what runs, the queue, what finished. `cauce_task` (read): one task's attempts and branch. `cauce_memory` (read): problems and every fix tried on them, in this workspace's repositories. `cauce_queue` (modify): one task into one repository's queue. `cauce_sessions` (read): every cauce session in these repositories with its work (`cauce overview`). `cauce_send` (send): a prompt into one of them, through tanka-link, after the user's yes. |
| Endings | `module.json` declares an event source: every 30 s the automation daemon runs `cli.py events`, which reads `cauce events --after <last> --kind finished --repo …` and prints one event per ending, for each workspace whose repositories it is in (`what`: "cauce task #12 in webapp ended blocked: … (why)"). The first poll only marks where it starts (`cauce events --last-id`), so old endings are no news; the cursor is `events.json` in `TANKA_CAUCE_HOME`. A trigger `--on cauce:*` turns each into a run of the assistant. Nothing is claimed: each session still hears of its own work. |
| Sessions | A cauce session and a [tanka-link](../link/README.md) session are the same Claude Code session id, so one joins the other: each session in the Sessions view shows the tasks it sent and, when tanka-link listens in it, a box to send it a prompt. The prompt goes in that session's inbox and it acts on it with its own permissions, as if you had typed it. Nothing reads or claims a session's endings: cauce still hands each session its own. |
| The page | `page.py` and `page.js` ([`docs/page.md`](../../docs/page.md)): the Code tab — what needs you, the **agents working**, what is pending per repository, what is done — with its badge, finished and stuck tasks in the chat, and the context the next message carries. Every task shows its **workflow**: the ladder of model × effort cells the router planned, where it started, where an attempt failed and moved on, where it passed, and the cell a worker runs in now; Details adds why it started there and each attempt's outcome and move. Its buttons are yours: cancel a task, reopen a paused repository, and **Run queue**, which starts `cauce work` for that repository, detached, logging to `work/<workspace>--<repo>.log`. |

### The Code tab

- **Projects.** A chip per repository this workspace may use; one is always selected, so the tab never
  shows every repository's cards at once. **Add project** lists
  every enrolled project — one with a `.cauce/` folder, which cauce creates the first time work is queued
  or run there, or `cauce init` does by hand — that this workspace does not use yet; *Use here* allows one.
- **Tasks.** Needs you, Agents working, Between attempts, Pending per repository, Done, each card with its
  title, its description and its workflow. A queued card says whether it runs beside the work going in
  its repository or waits its turn, and why — Haiku's call when cauce dispatches it. Work is not typed
  here: it is asked for in a Claude Code session.
- **Sessions.** The Claude Code sessions of the selected project — only an enrolled one, with a `.cauce/`
  folder, lists its sessions — each under the name Haiku gave it (yours, if you edited
  `.cauce/sessions.json`): the last prompt, how many,
  whether a task in it runs, and the command that resumes it where it ran (`claude --resume`), with Copy.
  Under it, the tasks that session sent and how each stands. With tanka-link in it, a box sends it a
  prompt (Send, then Confirm); without it, resuming opens it in your terminal. **New session** starts an
  interactive Claude Code in the selected project, inside a detached `tmux` session, on the first prompt you
  type (it needs `tmux` and `claude` on `PATH`); it shows here once cauce sees it, and `tmux attach -t …`
  (with Copy) lets you watch it. It runs with that project's usual permissions.
- **Stopped tasks.** A card that waits on you says what to do and, for a refused command, which rules a resume
  allows. **Resume** (*Allow & resume*, *Approve & resume*) runs `cauce resume <id> --pressed`: cauce reads the
  rules, approval or budget from its own account of the stop, never from the page. **Dismiss** takes it off
  the board (`cauce dismiss --via tanka`; a resume brings it back). **Ask its session** opens the Sessions view
  with a draft for the session that sent it, for you to edit and send.
- **Problems.** cauce's memory: every problem with every fix tried, whether it worked, failed, worked in
  part, or worked and then stopped holding, why, and the task and commit behind it. It searches as you
  type. *All of cauce* is the whole memory across repositories, because a dead end found next door is
  still a dead end; *These projects* keeps this workspace's. The page is yours and may read all of it;
  `cauce_memory` reads only this workspace's repositories.

An agent here is not a Claude Code subagent. It is a one-shot `claude -p` that cauce launches for one
attempt of one task, in one model × effort cell, with its own turn and dollar limits and only the MCP
servers that task needs; the tab shows each one out now with those limits, how long it has run and whether
its process is alive. A turn in your own Claude Code session shows apart, under *In your Claude Code sessions*.

The assistant cannot start the queue, cancel, resume, dismiss, start a session or merge: running the queue spends
money, so only a person starts it. Queueing spends nothing. Sending a session a
prompt is a `send`: the assistant shows the exact text and waits for your yes.

## Configuration

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_CAUCE_HOME` | `~/.tanka/shared/cauce` | `repos.json` and the queue runs' logs, outside every workspace |
| `TANKA_CAUCE_BIN` | found on `PATH` or in the plugin cache | The `cauce` program to run |
| `TANKA_CAUCE_TIMEOUT` | `20` | Seconds one cauce call may take |

## Commands

| Command | What it does |
| --- | --- |
| `tanka cauce allow <workspace> <directory> [name]` | Let the workspace see and queue in that repository (the name defaults to the directory's) |
| `tanka cauce deny <workspace> <name>` | Take it away |
| `tanka cauce repos <workspace>` | What it may use |
| `tanka cauce board <workspace>` | The board, as the assistant sees it |

## Files

| File | What it does |
| --- | --- |
| `cauce_link.py` | Finding and calling cauce, the scope, joining sessions with tanka-link, the text the tools print |
| `cli.py` | `tanka cauce allow\|deny\|repos\|board`, `events` for the automation daemon, and `post-install` |
| `page.py`, `page.js` | Its part of the page |
| `skill/` | `SKILL.md` and the tools |

Tests: `tests/test_cauce.py`, against a fake `cauce` that prints recorded JSON.
