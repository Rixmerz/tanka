# cauce

Your coding tasks on the page. [cauce](https://github.com/Rixmerz/cauce) routes
coding work to one-shot Claude Code workers, escalates on evidence and keeps
every attempt; this module shows its board in the **Code** tab of the page
(`tanka ui`) and gives the assistant three tools to read it and to queue new
tasks, in the repositories you allow for that workspace.

## Setup

```bash
claude plugin marketplace add Rixmerz/claude-plugins   # cauce itself, if it is not installed yet
claude plugin install cauce@rixmerz
tanka install cauce <workspace>                       # cauce_board, cauce_task, cauce_queue
tanka cauce allow <workspace> ~/code/webapp           # a repository this workspace may use
tanka ui <workspace>                                  # the page, Code tab
```

## How it works

| Piece | What it does |
| --- | --- |
| The program | Every read and write goes through the `cauce` program and the JSON it prints (`board --full --repo`, `show --json`, `queue add --json`). The module never opens cauce's database. It finds the program at `TANKA_CAUCE_BIN`, then on `PATH`, then in the newest Claude Code plugin install. |
| Scope | `repos.json` lists, per workspace, the repositories it may see and queue in, by a short name. A task in any other repository is refused before cauce runs, and the board asks cauce only for the allowed ones. No tool writes that file. |
| Tools | `cauce_board` (read): what needs you, what runs, the queue, what finished. `cauce_task` (read): one task's attempts and branch. `cauce_memory` (read): problems and every fix tried on them, in this workspace's repositories. `cauce_queue` (modify): one task into one repository's queue. |
| The page | `page.py` and `page.js` ([`docs/page.md`](../../docs/page.md)): the Code tab — what needs you, the **agents working**, what is pending per repository, what is done — with its badge, finished and stuck tasks in the chat, and the context the next message carries. Every task shows its **workflow**: the ladder of model × effort cells the router planned, where it started, where an attempt failed and moved on, where it passed, and the cell a worker runs in now; Details adds why it started there and each attempt's outcome and move. Its buttons are yours: cancel a task, reopen a paused repository, and **Run queue**, which starts `cauce work` for that repository, detached, logging to `work/<workspace>--<repo>.log`. |

### The Code tab

- **Projects.** A chip per repository this workspace may use, and *All projects*. **Add project** lists
  every repository cauce has worked in (from its tasks and the sessions it saw) that this workspace does not
  use yet; *Use here* allows one. cauce keeps no folder in a project: what it knows of a project is in its
  own database, so a project shows here once cauce has run or seen a session there.
- **Tasks.** Needs you, Agents working, Between attempts, Pending per repository, Done, each card with its
  title, its description and its workflow. **New task** queues one in the selected project by your hand.
- **Sessions.** The Claude Code sessions cauce saw in the selected projects: the last prompt, how many,
  whether a task in it runs, and the command that resumes it where it ran (`claude --resume`), with Copy.
  The page cannot attach to a session; resuming opens it in your terminal.
- **Problems.** cauce's memory: every problem with every fix tried, whether it worked, failed, worked in
  part, or worked and then stopped holding, why, and the task and commit behind it. It searches as you
  type. *All of cauce* is the whole memory across repositories, because a dead end found next door is
  still a dead end; *These projects* keeps this workspace's. The page is yours and may read all of it;
  `cauce_memory` reads only this workspace's repositories.

An agent here is not a Claude Code subagent. It is a one-shot `claude -p` that cauce launches for one
attempt of one task, in one model × effort cell, with its own turn and dollar limits and only the MCP
servers that task needs; the tab shows each one out now with those limits, how long it has run and whether
its process is alive. A turn in your own Claude Code session shows apart, under *In your Claude Code sessions*.

The assistant cannot start the queue, cancel or merge: running the queue spends
money, so only a person starts it. Queueing spends nothing.

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
| `cauce_link.py` | Finding and calling cauce, the scope, the text the tools print |
| `cli.py` | `tanka cauce allow\|deny\|repos\|board`, and `post-install` |
| `page.py`, `page.js` | Its part of the page |
| `skill/` | `SKILL.md` and the three tools |

Tests: `tests/test_cauce.py`, against a fake `cauce` that prints recorded JSON.
