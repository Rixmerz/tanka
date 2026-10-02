# Boards

Tables your assistant fills and you read on the page (`tanka ui`): a course's grades with the feedback each student will read, a client pipeline, anything with rows. On the page each board is grouped (by course, by client), every group counts its states ("12 reviewed · 3 pending"), and each row offers the buttons you declared. A button never acts: it writes its message in the chat's box, with the row's values, for you to read and send.

## Setup

```bash
tanka install boards <workspace>         # boards_rows, and an empty .claude/views/
tanka boards example <workspace>         # optional: copy the grades view to start from
tanka boards build <workspace>           # one tool per view: boards_record_<view>
tanka ui <workspace>                     # the Boards tab
```

Declare your own board with `/new-view` in `tanka dev <workspace>` ([`builder/skills/new-view`](../../builder/skills/new-view/SKILL.md)): it asks what one row is, writes the view in your language, checks it, builds its tool and tests it.

## A view

`<workspace>/.claude/views/<name>.json`, written by you, never by the assistant. [`examples/grades.json`](examples/grades.json):

| Key | What it is |
| --- | --- |
| `title`, `description` | The board's name, and what one row is in your words (the tool's description quotes it) |
| `fields` | At most 6, each `{label, type, hint}`: `text` (`long` for several lines), `number` (`min`, `max`), `choice` (`choices`, `editable` to change it on the page), `date` |
| `key` | 1-4 fields that say which row it is: recording the same key again updates that row |
| `group_by`, `columns` | How the page groups the rows, and which fields its table shows |
| `actions` | At most 4 buttons, `{label, says, when}`: `says` is the message, with `{field}` filled from the row; `when` shows it only for some choices |
| `example` | One complete row: the tool's example call |

Six fields because each one becomes a param of the view's tool, and a tool takes at most six (four of them required): the harness then checks every value (types, choices, bounds) before the tool runs, so the assistant never builds JSON by hand. A workspace has at most 5 views.

## How it works

| Piece | What it does |
| --- | --- |
| `boards_rows` | Read. With no view, the boards with their fields and choices; with a view, its rows, one line each (60 at most, `match` narrows). |
| `boards_record_<view>` | Draft. Made by `tanka boards build` from the view. Adds a row, or updates the fields given on the row with the same key: keys compare without case, accents or extra spaces ("ana perez" is "Ana Pérez"), and the row keeps the spelling it was first written with. It cannot delete. |
| Rows | `<TANKA_BOARDS_HOME>/<workspace>/<view>.json`, outside every workspace: the assistant reaches them only through its tools. At most 2000 rows a view. |
| The page | `page.py` and `page.js` ([`docs/page.md`](../../docs/page.md)): the Boards tab, a filter, a select on editable choices, archiving a row, and chips under an answer for the rows it recorded. |

Text from a row reaches the chat only through a button you press, and then as a message you can read and edit before it is sent: a row's values were written by a model from what it read (a submission, an email).

## Commands

| Command | What it does |
| --- | --- |
| `tanka boards check <ws>` | Whether every view can become a tool and a board |
| `tanka boards build <ws>` | Writes the tools and the list in the skill; run it after any view change. A tool whose view is gone is removed |
| `tanka boards show <ws> [view] [match]` | What `boards_rows` shows |
| `tanka boards example <ws>` | Copies the grades view |

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_BOARDS_HOME` | `~/.tanka/shared/boards` | The rows |

Tests: `tests/test_boards.py`.
