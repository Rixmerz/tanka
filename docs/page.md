# The page

`tanka ui [workspace]` opens a local page for your workspaces: a chat with each
assistant, what the routines and triggers reported, what the day cost, and
whatever the installed modules add. The page belongs to Tanka, not to a module;
modules plug into it.

| Module | What it adds to the page |
| --- | --- |
| [`desk`](../modules/desk/README.md) | **Your day**: reminders and pending checks beside the chat, fired reminders and the daily brief in it |
| [`boards`](../modules/boards/README.md) | **Boards**: tables the assistant fills (a course's grades, a client pipeline), declared in `.claude/views/` |
| [`codepanion`](../modules/codepanion/README.md) | **Notes**, **Sessions** and **Lenses**: what it noticed in your coding sessions, and their timelines |

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

Each message is one `tanka run` in the workspace (at most 10 turns and 0.30 USD, one at a time, 40 a
day). The day's messages share one Claude Code session, so it remembers what was said earlier today;
a new day starts a new session opened with the end of the earlier chat. Each message also carries
what the page showed on its own since the user's last one (a fired reminder, a note, a report), with
ids, so a reply like "done" has something to refer to. The run streams; the page shows the answer as
it is written.

The chat file holds the user's messages (`who: "you"`), the answers (`"tanka"`), failed answers
(`"error"`), and whatever a module writes there with `tanka_common.chat_event` (a fired reminder,
the brief).

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
| `tabs` | `[{id, label, render(sc, mod) → nodes, badge(sc, mod) → number}]` |
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
`focus(id)` and `render()`.

Module text is set with `el`, which never parses HTML. A module must not use `innerHTML`.
