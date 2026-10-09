---
name: approvals
description: How the tools that only ask work. Some tools in this workspace never act on their own: they leave a request with the exact text, and the user presses Approve in the chat. Read it when a tool says a request is waiting for the user.
---

# Approvals

Some tools in this workspace's skills only ask. They save a request with the
exact text and return "NOTHING was done". The chat shows that text to the
user with **Approve** and **Dismiss**; only their click runs it, once.

## Rules

- After such a tool, tell the user the request waits for their click in the
  chat. Never say it is done before they approve.
- Show the exact text before asking, and ask only after the user agreed to it.
- Never ask twice for the same thing; a waiting request stays on the page.
- If the user dismisses it, nothing happened. Ask again only if they say so.
- You cannot approve: there is no tool for it, on purpose.

## Which tool

| The user asks | Tool |
| --- | --- |
| did it go through? what was created? | `approvals_list` |
