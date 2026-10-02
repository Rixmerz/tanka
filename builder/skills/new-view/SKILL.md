---
name: new-view
description: Declare or reshape a board — a table the assistant of the Tanka workspace in TANKA_WORKSPACE fills and the user reads on the page, grouped and counted (a course's grades with feedback per student, a client pipeline, orders, anything with rows). Use when the user says "crea un tablero", "quiero ver por curso / por alumno / por cliente", "make a board", "una vista con las notas", wants to change a board's fields, states or buttons, or asks why a board's tool is missing.
argument-hint: "[what one row is]"
---

# New view

A board is one view, `.claude/views/<name>.json` in the workspace, that the boards module turns into a typed tool (`boards_record_<name>`) and a tab on the page. You write the view; the assistant never can. Read `modules/boards/README.md` first, and start from `modules/boards/examples/grades.json`: copy its shape, never install it unchanged.

The workspace is `$TANKA_WORKSPACE`; its name is the directory's name. All commands run from the repository.

## 0. Is it ready?

- `.claude/skills/boards` must exist in the workspace. If not: `bin/tanka install boards <name>`.
- Each board costs one tool, plus one for `boards_rows`, out of the workspace's 15. Run `bin/tanka tools check <name>` and tell the user how many are left before you add one. If there is no room, say which skills hold the tools and let them choose what to merge or drop; never drop one yourself.
- Read `.tanka/persona.json`: its `language` is the language of the labels, choices and button messages, because the user reads them on the page and in the chat box.

## 1. Ask, one question at a time

1. What is one row? Get it in one sentence ("la nota y el comentario de un alumno en una evaluación de un curso"). That sentence becomes `description`.
2. What tells two rows apart? Those are the key fields, 1-4 of them (course + student + evaluation).
3. What else does a row hold? At most 6 fields in all, key included. For each: text (one line, or `long` for a paragraph), a number with its range, a choice from a short list, or a date. More than 6 means two boards, or a field the row does not need.
4. How do they want to see it grouped (`group_by`), and which states they want counted at a glance (the choice fields; mark the one they change by hand `editable`).
5. What do they do with a row afterwards? Each becomes a button (at most 4): its `says` is the message it writes in the chat for them to send ("Revisa de nuevo la {evaluation} de {student} en {course}…"), and `when` shows it only for some states. A button never acts by itself; say so when you propose one.

## 2. Write

`.claude/views/<name>.json`, the name 2-24 lowercase letters, digits or `_`:

- `title`, `description`, `key`, `group_by`, `columns` (the table's columns; leave the group out), `fields`, `actions`.
- Each field: `label` and `hint` in the user's language (the hint is what the tool tells the assistant about the field), `type`, and `min`/`max`, `choices`, `long` or `editable` as it applies.
- `example`: one complete, realistic row with made-up names. The assistant sees it as the tool's example call.

## 3. Check and build

1. `bin/tanka boards check <name>` → ok. Fix every problem it names; do not weaken the view to pass it.
2. `bin/tanka boards build <name>`. It writes `boards_record_<view>` and lists the board in the skill. Never edit those generated files: change the view and build again.
3. `bin/tanka tools check <name>` → the new tool loads, with no problem.
4. `bin/tanka tools test boards_record_<view> '<the example row>' <name>`, then `bin/tanka boards show <name> <view>`: the row is there. Run the same call again with one field changed: the row is updated, not doubled.

## 4. Hand it over

Tell them what was built, the tools left out of 15, and that they see it with `bin/tanka ui <name>` → Boards. The test row from step 3 is theirs to archive with its ×, or tell them to ask the assistant to fill the board for real.

To reshape a board later, edit the view and run steps 3 and 4 again. Renaming a view starts an empty board: the old rows stay under the old name.
