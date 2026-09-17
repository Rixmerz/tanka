---
name: plan
description: Define, review or close the objective of a delegated task before acting — done-criteria, allowed tool classes, action budget, whether sending is permitted. Use before any task with more than three actions or any send, when the user says "take care of", "do this for me", "task", "plan", or "plan close".
allowed-tools: Read, Write, Glob
argument-hint: "[task description | close | show]"
---

# Task objective

The objective lives in `.tanka/state/objective.json`. The harness uses it to block out-of-scope tools, cap the number of actions and require a verifiable closure. A vague objective is a task nobody can check. Be concrete.

## If `$ARGUMENTS` is `show`
Read the file and summarise it in five lines (title, goal, criteria, permissions, status). Stop there.

## If `$ARGUMENTS` is `close`
Read the file, ask whether the outcome is `done`, `blocked` or `cancelled` if it isn't obvious, set `status`, and add `closed_at` (ISO) and `outcome` (one sentence). Finish with the matching `Status:` line. Stop there.

## Otherwise: create the objective

1. If an objective with `status: active` already exists, show it and ask whether to replace it or continue. Never overwrite an active objective without permission.
2. From the description (`$ARGUMENTS` or the user's last request), write the objective. If something essential is missing — who, what, by when, what counts as finished — ask ONE question with options. Two questions maximum, ever.
3. Pick the smallest set of tool classes that can do the job:
   - `read`: read email, files, calendar, search.
   - `draft`: create drafts (never sends).
   - `modify`: label, archive, move, update (reversible).
   - `send`: send, reply, forward, post, invite (irreversible, always confirmed).
   - `destructive` is never granted by an objective.
4. Write the file in this shape. The harness validates it and rejects invalid fields:

```json
{
  "id": "2026-09-17-triage-inbox",
  "title": "Triage today's inbox",
  "goal": "Classify today's unread mail as urgent/normal/ignore and prepare replies for the urgent ones",
  "done_when": [
    "Every unread message from today has a label",
    "Each urgent message has a reply saved as a draft",
    "The user has been shown a summary table"
  ],
  "allowed_tool_classes": ["read", "draft", "modify"],
  "allowed_tools": [],
  "may_send": false,
  "recipient_allowlist": [],
  "max_tool_calls": 30,
  "status": "active",
  "created_at": "2026-09-17T09:00:00Z",
  "notes": ""
}
```

   - `allowed_tools` empty means any tool of the allowed classes; fill it and only those are permitted (names like `mcp__server__tool`).
   - `may_send: true` only if the user explicitly said something should be sent in this task.
   - `max_tool_calls`: a realistic estimate times two, never above 60.

5. Show the objective in four to six lines and ask for an OK. Only then start executing, step by step, checking each `done_when` criterion as you go.

## While executing
- Before each action, check: is it within an allowed class? did I already do it? do I have every detail?
- If the harness blocks an action, do not retry it unchanged: read the reason, fix it, or ask.
- When you finish or get stuck, run `/tanka:plan close` and end with the `Status:` line.
