---
name: cauce
description: The user's coding tasks, run by cauce in their repositories — what needs them, what is running, what is queued, and queueing a new one. Use it whenever the user asks about their code, a coding task, a repository, a branch to review, what failed, or asks to fix, build, change, document or review something in a repository.
---

# cauce

cauce runs the user's coding tasks in workers, in the repositories this
workspace may use, and keeps every attempt. You see the tasks and you queue new
ones. You do not run the queue: the user starts it on their page.

## Which tool

| The user asks for, or you need | Tool |
| --- | --- |
| what needs them, what is running, what is queued, what finished, which repositories exist | `cauce_board` |
| why one task failed, what it tried, its branch | `cauce_task` |
| to fix, build, change, document or review something in a repository | `cauce_queue` |

## Recipes

**The user asks what is going on with their code:**

1. `cauce_board`, with the repository if they named one.
2. Say first what needs them, then what runs; one line each.

**The user asks why a task failed, or about one task:**

1. The task id from `cauce_board`, unless they gave it.
2. `cauce_task` with that id; say what it tried and what it asks of them.

**The user wants something done in a repository:**

1. The repository: the name they gave, as `cauce_board` lists it. None given and
   more than one exists → ask which.
2. The task: what they said, in their words, complete enough to act on alone.
3. If you had to pick the repository or reword the task, confirm both in one line.
4. `cauce_queue`; say its number and that it runs when they start the queue on the page.

## Rules

- Never queue the same task twice. A queued task that succeeded is done.
- Never invent a repository name or a test command: ask.
- You cannot start, cancel or merge anything. Those are the user's buttons on the page.
- Branch names, costs and model names come from the tools only.

## Out of scope

- Running the queue, cancelling a task, unpausing a repository, merging a branch:
  say the user does it on the page, in the Code tab.
- A repository this workspace may not use: say the user can allow it from a
  terminal with tanka cauce allow.
- Writing or reading code yourself: there is no tool for it here; queue a task.
