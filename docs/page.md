# The page

`tanka ui [workspace]` opens a local page for your workspaces: a chat with each
assistant, what the routines and triggers reported, what the day cost, and
whatever the installed modules add. The page belongs to Tanka, not to a module;
modules plug into it.

| Module | What it adds to the page |
| --- | --- |
| [`desk`](../modules/desk/README.md) | **Your day**: reminders and pending checks beside the chat, fired reminders and the daily brief in it |
| [`boards`](../modules/boards/README.md) | **Boards**: tables the assistant or the workspace's own tools fill (a course's grades, a client pipeline), declared in `.claude/views/` |
| [`whatsapp`](../modules/whatsapp/README.md) | **People**: the contacts of the workspace's roles, grouped, with what the assistant keeps about each, and their roles and permissions |
| [`codepanion`](../modules/codepanion/README.md) | **Notes**, **Sessions** and **Lenses**: what it noticed in your coding sessions, and their timelines |
| [`cauce`](../modules/cauce/README.md) | **Code**: the coding tasks [cauce](https://github.com/Rixmerz/cauce) runs, per project — what needs you, the workers out now, what is pending, what is done, each with its description and its way through the model × effort ladder; the Claude Code sessions seen in each project, with the command that resumes them; and cauce's memory of problems and fixes — and finished or stuck tasks in the chat |
| [`routines`](../modules/routines/README.md) | **Routines**: the assistant's proposed routines waiting for you, each with its schedule, budget, reason and full task, to approve or reject; the routines that run (with their last report, to remove) and the triggers; and what was decided lately |

A workspace with none of them still has the chat.

## Privacy

The page shows prompts, notes, grades and whatever else the workspaces hold, so it is private:

- it listens on 127.0.0.1 only and refuses a request whose `Host` is not that address, so a page
  reached through DNS rebinding cannot talk to it;
- the page needs the random token in the URL `tanka ui` prints, and every API call sends it in a
  header (`X-Tanka-Token`) that another origin cannot set without a preflight the server never grants;
- a strict Content-Security-Policy with one nonce, and every text is set as text, never as HTML.

Its data lives in `~/.tanka/shared/` (`TANKA_PAGE_HOME` for the chat and the page's token), outside
every workspace: the assistant can write to its workspace's `.tanka/`, and anything there could be
forged without going through a tool.

## The chat

Each message is one `tanka run` in the workspace. Nothing is limited per message: the assistant spends at
most `TANKA_PAGE_DAY_USD` a day (5), and a run may use what is left of it. A message sent while it answers
stops that answer and takes its place; past `TANKA_COMPACT_TOKENS` of context (90000) the session is
compacted before the next message, keeping the task and the decisions, and `/compact` does it on demand. The day's messages share one Claude Code session, so it remembers what was said earlier today;
a new day starts a new session opened with the end of the earlier chat. A session also starts over when
the assistant's skills or tools changed since it began (a tool Dev just built, a module installed): resuming
would keep it answering from the old ones, as in "that parameter does not exist". The new one opens with the
conversation so far, flagged as possibly stale about tools, and the chat shows a line saying it restarted. Each message also carries
what the page showed on its own since the user's last one (a fired reminder, a note, a report), with
ids, so a reply like "done" has something to refer to. The run streams; the page shows the answer as
it is written.

The chat file holds the user's messages (`who: "you"`), the answers (`"tanka"`), failed answers
(`"error"`), and whatever a module writes there with `tanka_common.chat_event` (a fired reminder,
the brief).

## Files

A file dropped on the chat, or picked with the clip beside the box, is copied at once into the workspace's
`files/` folder (`POST /api/files?scope=&name=`, the file's raw bytes as `application/octet-stream`, at
most `TANKA_PAGE_UPLOAD_MAX_MB`, 25). It never lands over another file (a second `report.pdf` is
`report (2).pdf`), and its name cannot leave the folder or hide (`../x` is `x`, `.env` is `env`). Until the
message goes, each file shows as a chip; the message names them (`files` in `POST /api/chat`, at most 10,
each one a file in `files/`), the bubble shows them, and the assistant is told to read them with Read.
Taking a chip off the message leaves the file in the workspace. Reading a file the user added needs no
skill, and the per-turn reminder says so.

## Folders outside the workspace

The assistant reads its own workspace. Anything else it reads only from a folder the user approved, read
only. When it tries to read outside (Read, Glob, Grep), the `PreToolUse` hook checks the folder against
`read_dirs` in `.tanka/policy.json`, which the assistant cannot write:

- approved: the read is allowed, and the launcher already passed the folder to Claude Code as `--add-dir`;
- not approved: the read is denied and the hook leaves a **request** for that folder, outside every
  workspace (`TANKA_PAGE_HOME/access/`), so the assistant cannot approve itself. The chat shows it where it
  happened, with *Approve, read only* and *Deny*; approving adds the folder (or one above it, by
  `POST /api/access {scope, id, decision, dir?}`) and the next message reads it;
- never: the whole disk, the home folder itself, `~/.ssh`, `~/.aws`, `~/.gnupg`, `~/.claude`, `~/.tanka`
  (every workspace's data and the page's), `~/.config/gcloud`, `~/.kube`, `~/.docker`, or any folder that
  holds one of them. These are denied without a request, whatever the policy says.

A path is resolved before it is checked, so a link inside the workspace cannot lead out of it. Writing
outside the workspace stays denied. Workspace → Rules lists the approved folders, to add or remove by hand.

## Commands

Typing `/` as the first thing in the box, or the **/** button beside it, opens the commands that mode can
use, each with what it takes and what it is for (`GET /api/commands?scope=`): Tanka's own skills
(`/tanka:plan`, `/tanka:draft`…) and the workspace's skills for the assistant, the builder's
(`/tanka-dev:new-skill`, `/tanka-dev:new-view`…) for Dev. Typing filters them, the command's own name first;
arrows move, Enter or Tab picks and Escape closes. Picking one puts it in the box for the user to finish
and send; the menu never sends. A skill whose frontmatter says `user-invocable: false` is left out.

## Dev, from the chat

A selector over the chat's box picks who answers: **Tanka**, the workspace's assistant, or **Dev**, the
user's strong model (`TANKA_DEV_MODEL`, default `opus`), which builds what the assistant uses. It starts
on Tanka each time the page opens. Dev has its own session of the day and its own conversation in the
same chat, spends at most `TANKA_DEV_PAGE_DAY_USD` a day (10), and what it makes shows on the page at once: a board within seconds, a new tool on the
assistant's next message.

What dev may touch is decided by a PreToolUse hook, [`tanka_dev_guard.py`](../plugin/scripts/tanka_dev_guard.py):
read the repository and the workspace; write the workspace's `.claude/views`, `.claude/skills`,
`.claude/codepanion` and its `codepanion.json` and `desk.json`; run only the `bin/tanka` commands that
check and build those (`boards`, `tools check|test|list`, `codepanion check`, `modules`, `install`,
`desk brief`), one per call, with no shell operators or substitutions. Everything else is denied, and an
error in the guard denies too. The run itself is `bypassPermissions`, because Claude Code asks before
any write under `.claude/` and a headless run cannot answer; the guard is what holds it, as the Tanka
hooks hold the assistant. It loads none of the user's settings, hooks or MCP servers. A tool that needs
a browser or another program is built with `tanka dev <workspace>` in a terminal.

New tools reach open terminal sessions too: the Tanka MCP server tells Claude Code when the tools change
(`tools/list_changed`), so no `/mcp` reconnect is needed. A **new** skill needs `/reload-skills` in an
open session, which no hook can run: the next prompt shows a line saying so.

## The Workspace tab

Every workspace has a **Workspace** tab, after the modules' tabs and before Health. It is part of the
page, not a module, and loads its data on its own while it is open (`GET /api/workspace`), so
`/api/state` stays small.

**The persona.** Its picture and its name. The name is saved into
`<workspace>/.tanka/persona.json` (1 to 40 characters, one line), keeping every other key there. The
picture is uploaded as base64 (`POST /api/persona/avatar`) and kept by what its bytes are, whatever the
upload claims: PNG, JPEG or WebP, at most 512 KB; SVG, GIF and anything else are refused, and a body over
800 000 bytes is refused with 413 before it is read (every other route reads at most 10 000 bytes). It is
stored at `~/.tanka/shared/page/avatars/<workspace>.<png|jpg|webp>` (`TANKA_PAGE_HOME`), outside every
workspace, so the assistant cannot change its own face. `/api/state` carries only its version
(`avatar_v`, the file's mtime); the page fetches the picture once per version
(`GET /api/persona/avatar` returns a `data:` URL, the only images the CSP allows) and shows it wherever
the persona appears: its messages, the typing indicator, the greeting of an empty chat and the workspace
switcher. `POST /api/persona/avatar-remove` goes back to the initial.

**Profile and Rules.** Every parameter of the workspace, by hand. *Profile* is the rest of
`.tanka/persona.json` — your name, the language (set here, the first session no longer asks), time zone,
tone, personality, answer format, signature, notes — and the advisor model in `.claude/settings.json`.
*Rules* is `.tanka/policy.json`: what the assistant may do without asking for each class of tool (read,
drafts, changes, sending, unclassified), whether other MCP servers load, the checks before anything is
sent (subject, shortest message, most recipients, allowed and blocked recipients, your own domains), the
limits against loops, and the closing status line. `POST /api/workspace/settings` checks every value
before it writes anything, and keeps every key the page does not show. Destructive tools are not
offered: they are always denied, and a request to allow them is refused. A field being typed in survives
the page's polls; Save loads the tab again from what was written.

**Reload.** Each chat message is a new `claude -p` process, so it already loads the skills and starts
the MCP servers as they are on disk. The Reload card proves it before the next message, and gets out of a
conversation that still believes something old. *Reload skills* scans the workspace again and lists every
skill with its tools, the tools that will load out of 15, and every problem and warning; it also refreshes
the `/` menu. *Reload MCP servers* starts each server the next message will start — Tanka's own, then the
ones in `.tanka/mcp.json` when the rules let them load — the way Claude Code does (`initialize`, then
`tools/list`, at most `TANKA_PAGE_MCP_PROBE_SECONDS`, 10, each), lists their tools or why one failed, and
stops them; a remote server is named but not contacted. *New conversation* drops the day's session for the
mode on screen, so the next message starts one. `POST /api/workspace/reload {scope, what: skills|mcp|conversation, mode?}`.
Open terminal sessions are not reached: there, a new skill still needs `/reload-skills`.

*Restart everything* reloads absolutely all of it, for when something still behaves as before: every
conversation of the workspace (the assistant's and Dev's) starts over, the automation daemon restarts, and
the page's own process is replaced by a new one on the same port and with the same token
(`POST /api/restart {scope}`), so its code and every module's `page.py` load again. The open tab waits
for it to come back and reloads itself. It is refused while an answer is running, which it would cut off.

**A new workspace.** The workspace switcher ends in *New workspace*: a folder name and the assistant's
name, and the page runs `tanka init` exactly as a terminal would (`POST /api/workspace/create`), then
opens on it.

**The tools and skills.** A bar with the tools in use out of 15, and every skill in
`.claude/skills/` (on) and `.claude/skills.off/` (off), with its tools and where it came from: a
module's (its directory carries the `.module.json` mark `tanka install` leaves, or, for an older install,
it has the module's name and every tool file the module ships) or the workspace's own. An own skill has a
switch that moves its directory between the two folders; turning one on is refused when it would take
the workspace past 15 tools. The page never deletes an own skill, and a module's skill is not switched:
the module is installed or removed.

**The modules.** Every module in `modules/`, with its description, its tools, whether it is installed,
what it needs, what installed module needs it, the programs missing on this machine, and its setup
command (`tanka <module>`) when its `cli.py` has commands of its own. **Install** runs
`tanka install` (what it needs first, never past 15 tools) and shows what it printed, the module's
`post-install` included. **Remove** runs `tanka uninstall`. Both ask once more before they act.

**What Remove does.** It removes the module's skill, that is its tools, from the workspace, and nothing
else: its settings there (`.claude/<module>.json`, lenses, views) and its data in `~/.tanka/shared/`
stay, so installing it again picks up where it was. A module's panel on the page follows its own
`installed()` rule, so it may keep showing while it has data. Remove is refused while another installed
module needs it ("codepanion needs desk: remove codepanion first"), and for a skill that is not the
module's. A module's `cli.py post-remove <workspace> <scope>` runs when it has one.

After a change the tab says when it takes effect: the assistant sees it on its next message; open
terminal sessions update their tools by themselves, and a new skill needs `/reload-skills` there.

| Route | What it does |
| --- | --- |
| `GET /api/workspace?scope=` | the persona, `settings` (profile, rules, advisor), `tools_used`/`tools_max`, `skills`, `modules` |
| `POST /api/workspace/install` `{scope, module}` | install a module (and what it needs) |
| `POST /api/workspace/uninstall` `{scope, module}` | remove a module's skill |
| `POST /api/workspace/skill` `{scope, skill, enabled}` | turn an own skill on or off |
| `POST /api/workspace/settings` `{scope, persona?, policy?, advisor_model?}` | save the profile, the rules or the advisor |
| `POST /api/workspace/create` `{scope, name?}` | a new workspace (`tanka init`) with its assistant's name |
| `POST /api/workspace/reload` `{scope, what, mode?}` | rescan the skills, start and ask the MCP servers, or start a new conversation |
| `POST /api/access` `{scope, id, decision, dir?}` | approve (read only) or deny the assistant's request to read a folder |
| `POST /api/restart` `{scope}` | restart the conversations, the automation daemon and the page itself |
| `POST /api/persona/name` `{scope, name}` | rename the persona |
| `POST /api/persona/avatar` `{scope, type, data}` | set the picture |
| `POST /api/persona/avatar-remove` `{scope}` | remove the picture |
| `GET /api/persona/avatar?scope=` | `{"data": "data:image/…;base64,…"}` or `{"data": null}` |

## How a module plugs in

A module that adds to the page ships two files next to its `module.json`.

### `page.py`

Imported by the page server. Every function is optional except `installed`.

| Name | Returns | Used for |
| --- | --- | --- |
| `installed(ws)` | `bool` | whether the module is set up in that workspace; the other hooks run only then |
| `state(scope, ws)` | `dict` | the module's part of `/api/state`, at `scopes[i].modules[<name>]` |
| `stream(scope, ws)` | `list[dict]` | items merged into the chat by time; each has `t` and a `who` the module owns |
| `effects(scope, ws, spans)` | `list[list[dict]]` | for each `(t0, t1)` span of an answer, the chips it shows (what the answer added or changed) |
| `context(scope, ws, since, until)` | `list[tuple[float, str]]` | lines the next message carries: what the module said on its own in that window |
| `hint(scope, ws)` | `str` | instructions appended to the chat's system prompt; name only this module's tools |
| `health()` | `list[dict]` | rows for the Health tab: `{"label", "ok", "text"}` |
| `ACTIONS` | `dict[str, fn(scope, ws, body)]` | `POST /api/m/<module>/<name>`; returns JSON-able data |
| `GETS` | `dict[str, fn(scope, ws, query)]` | `GET /api/m/<module>/<name>` |

An action changes only what the user is allowed to change by hand (tick, snooze, archive, rate).
Anything that acts in the world (publish a grade, send a message) is a chat message instead: the
assistant does it with its tools, through the same rules as any other request.

### `page.js`

Inlined into the page's single script, inside its nonce, so the Content-Security-Policy stays as it
is. It calls `Tanka.module(name, spec)` once; `spec` may hold:

| Key | What it is |
| --- | --- |
| `tabs` | `[{id, label, render(sc, mod) → nodes, badge(sc, mod) → number, sig(sc, mod) → any}]`; with `sig`, the tab is rebuilt only when it changes, so an input on it keeps its focus between polls |
| `items` | `{who: (sc, m, mod) → Node}`: how the module's chat items look |
| `chips` | `(sc, f, mod) → Node`: one chip under an answer, for this module's effects |
| `side` | `{render(sc, mod) → nodes, label(sc, mod) → nodes, sig(sc, mod) → string}`: the panel beside the chat (one module at most) |
| `attention` | `(sc, mod) → number`: added to the chat tab's badge and the window title |
| `using` | `(toolName) → string or null`: what the chat says while the answer uses one of its tools |
| `suggest` | `[[text, sendNow]]`: buttons over the composer |
| `onChat` | `(sc, mod) → void`: called when the chat is on screen (mark notes seen) |
| `hello` | `(sc, mod) → string`: added to the greeting of an empty chat |
| `refresh` | `async (sc, mod) → void`: called after each state fetch, for data a tab loads on its own |

`Tanka` also gives the helpers every module needs: `el`, `svg`, `mark`, `api`, `act`, `T` (the core's
strings), `hm`, `when`, `clock`, `isToday`, `shortDay`, `relOf`, `rich`, `plural`, `nameOf`,
`focus(id)` (flash an element of the side panel), `go(tab)`, `render()`, `refresh()`, and
`compose(text)`, which opens the chat with that text in the box for the user to read and send: the
way a button asks the assistant for something.

Module text is set with `el`, which never parses HTML. A module must not use `innerHTML`.
