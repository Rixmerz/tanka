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

## A board without tools

A workspace can have a board without the boards skill: a view in `.claude/views/` is enough for the page to show it, and the workspace's own tools fill it as they work. A workspace with all 15 tool slots taken still gets its board this way, at no slot cost: the grading tools of a course workspace write each student's grade when they set it, and the comment when they publish it.

A tool script writes a row with `boards.try_record`, after its real action succeeded:

```python
import os, sys
from pathlib import Path

try:
    sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "boards"))
    import boards
    boards.try_record("myworkspace", "grades", {"course": "Programming I", "student": "Ana Pérez",
                                                "evaluation": "EVA2", "grade": 6.2, "status": "published"})
except Exception:  # the board never changes what the tool did or says
    pass
```

| Function | What it does |
| --- | --- |
| `boards.record(scope, view, args, by="assistant")` | Adds or updates the row, as `boards_record_<view>` does; raises `ToolError` with what is wrong (a missing key field, a value off the view's scale, no such view). Returns `(row, changed)` |
| `boards.try_record(scope, view, args, by="assistant")` | The same, but it never raises: it returns `False` and says why on stderr, which the harness ignores when the tool exits 0. `None` values are left out, so a tool passes what it has |

- `scope` is the workspace's name as Tanka knows it (`tanka ui <name>`), not its directory's: a workspace that is a symlink keeps its rows under the name.
- Pass `by` other than `"assistant"` from work that runs in the background (a worker that outlives the tool call): the page puts a chip under an answer for the rows `"assistant"` changed while it ran.
- Without the skill the assistant has no `boards_rows` and no `boards_record_<view>`, so the chat's system prompt does not mention boards. A view's buttons must then ask for what the workspace's own tools do.
- `tanka boards check` works without the skill; `tanka boards build` needs it, because it writes tools.

Text from a row reaches the chat only through a button you press, and then as a message you can read and edit before it is sent: a row's values were written by a model from what it read (a submission, an email).

## Commands

| Command | What it does |
| --- | --- |
| `tanka boards check <ws>` | Whether every view can become a tool and a board; works without the skill |
| `tanka boards build <ws>` | Writes the tools and the list in the skill; run it after any view change. A tool whose view is gone is removed. Needs the skill |
| `tanka boards show <ws> [view] [match]` | What `boards_rows` shows |
| `tanka boards example <ws>` | Copies the grades view |

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_BOARDS_HOME` | `~/.tanka/shared/boards` | The rows |

Tests: `tests/test_boards.py`.
