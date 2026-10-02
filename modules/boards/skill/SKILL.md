---
name: boards
description: Recording rows on the user's boards and reading them — a course's grades and feedback per student, a client pipeline, any table the user declared as a view. Use it when the user asks to record, update or look at something on a board, asks who is missing, what is pending on a board, or how a row stands, and when a task you are doing produces a row for one.
---

# Boards

The user reads these boards on their page, grouped and counted, and acts on
a row from there. You fill them. Each board is a view the user declared;
you cannot make, rename or delete one.

## Which tool

| The user asks for, or you need | Tool |
| --- | --- |
| which boards exist, their fields and choices | `boards_rows` with no view |
| what a board holds, who is missing, a row's state | `boards_rows` with the view (and match to narrow it) |
| recording or changing a row | the board's own tool, listed below |

## Recipes

**Recording:** when you do not know the boards yet, `boards_rows` with nothing
first. Then one call per row to its tool, with the key fields that say which
row it is and only the fields you know. Calling it again with the same key
updates that row; leave a field out to keep what it has.

**Answering from a board:** `boards_rows` with the view; pass match (a course,
a name) when it has many rows. Answer from what it lists, with numbers.

**A message that names a row** usually comes from a button on the page: the
user read it and sent it. Do what it says for that row, with your tools, and
record the result on the board.

## Rules

- Write what you did or read, not what you assume: a grade you did not
  compute, a status you did not check, stays out.
- Feedback and comments go in the words the user will see; keep them in the
  user's language unless the board is for someone who reads another.
- You cannot delete a row: the user archives it on the page.

## Boards in this workspace

<!-- views: written by tanka boards build -->

No boards yet.
