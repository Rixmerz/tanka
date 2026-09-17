---
name: triage
description: Classify and prioritise email, messages, tasks or documents against an explicit rubric with a confidence level, and apply labels only with permission. Use when the user says "go through my inbox", "what's urgent", "classify", "sort", "prioritise", "summarise what's pending".
allowed-tools: Read, Write, Glob
argument-hint: "[what to review and by which criteria]"
---

# Triage against a rubric

A small model classifies well when the rubric is explicit and the batch is small. Work in batches of at most 15 items; above that, ask the user to narrow it down (date, sender, folder).

## 1. Rubric
If the user gives no criteria, propose and confirm this one — one line per category, with an example:

| Category | Criterion | Example |
|---|---|---|
| URGENT | Needs the user to act within 24h, or comes from someone key | "I need your OK today" |
| ACTION | Needs a reply or a task, no immediate deadline | a meeting request |
| INFO | Read only, nothing to do | internal newsletter, confirmation |
| IGNORE | Promotional, obvious spam, automated with no value | offers, repeated notifications |

If `.tanka/persona.json` has `notes` naming priority people or topics, treat those as an URGENT signal.

## 2. Read
Use read tools only. For each item note: sender, subject, date, one line of why that category, and a **confidence** (high/medium/low). Low confidence means ask the user rather than decide.

## 3. Present
A table with: #, sender, subject, category, confidence, suggested next step (one to five words). Then one line with the count per category.

## 4. Act (only if the user asks, or the objective authorises it)
- Labelling and archiving are `modify` class: the harness will ask for confirmation per batch. Tell the user that is expected.
- Never delete and never mark as spam. Offer archiving instead.
- For the URGENT ones, offer to prepare drafts with `/tanka:draft`. Never send anything from triage.

## 5. Close
```
Status: done | partial
Done: N classified, M labelled
Left: K low-confidence items waiting on your call
```
