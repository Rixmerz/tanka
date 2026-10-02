---
name: desk
description: Keeping the user's day — reminders at a time and pending checks per topic, which they see beside the chat on their page. Use it whenever the user says they have something pending, asks to be reminded of something, says they finished something, or asks what they have pending today, on a project or on a subject.
---

# Desk

You keep the user's cards. A **reminder** is one thing at one time: they get a
desktop notification then, and it shows in their chat. A **check** card holds
the pending items of one topic: a project or any subject they name. They see
both in **Your day**, beside the chat on their page, where they tick, snooze
and archive them. You add and tick; you never delete.

## Which tool

| The user asks for, or you need | Tool |
| --- | --- |
| what is pending, today's checks, the reminders, an item's id | `desk_pending` |
| something pending on a topic, a reminder at a time, ticking one | `desk_card` |

## Recipes

**The user says they have to do something, or asks to be reminded:**

1. Something with a time ("recuérdame a las 5…") → `desk_card` kind reminder,
   with at. No time said → ask when; never pick one.
2. Something pending with no time ("tengo que…", "me falta…") → `desk_card`
   kind check, with the project or subject as topic.
3. Confirm in one line with the id it returns.

**The user says they finished something:**

1. `desk_pending` (with the topic, if they named it) and find that item or reminder.
2. Exactly one open item or reminder matches → `desk_card` with done true and
   its id as text, right away: they already said it is done, do not ask again.
   None or several match → ask which.

**You find something left pending on your own** (in a coding session, a
message): `desk_card` kind check, with its topic and what it comes from as
evidence. These are rationed per hour; a refusal means stop and say nothing.

## Rules

- Cards are the user's list. Add only what they said, or what you found with
  its evidence; never tick something they did not say they finished.
- You cannot delete or archive cards: the user does that on the page.
- A reminder needs a time the user gave. Do not guess one.
