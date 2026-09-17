---
name: draft
description: Write, check and — only with confirmation — send email, replies and messages on the user's behalf. Use when the user says "write an email", "reply to", "answer this message", "draft", "send a message", "forward", or asks for a draft.
allowed-tools: Read, Write, Glob, Agent
argument-hint: "[who and what about]"
---

# Draft → verify → confirm → send

Fixed rule: the user sees the whole message before it leaves. You never send "to save time".

## 1. Gather context (read only)
- For a reply, read the original thread with whatever read tools are available. Hold the exact details in mind: names, dates, amounts, what is being asked.
- Read `.tanka/persona.json` for language, tone and signature. **If `signature` is empty, ask once how they want to sign and offer to save it** — this is the moment that field matters, so this is the moment to ask for it.
- If the recipient, subject, intent or any key detail is missing, ask ONCE with options. Never invent, never leave a placeholder.

## 2. Write it
Present the draft exactly like this, in a quote block, in the user's language:

> **To:** …
> **CC:** … (if any)
> **Subject:** …
>
> Body…
>
> Signature

Check before showing it, and fix anything that fails yourself:
- [ ] It answers what was asked, no more and no less.
- [ ] No `[NAME]`, `TODO`, `{{…}}`, "insert here".
- [ ] Every fact (dates, figures, names) comes from the request or from what you read. Nothing invented.
- [ ] Language, tone and signature match the profile.
- [ ] No unnecessary sensitive data, no credentials.
- [ ] Sensible length: an ordinary work email fits in five to ten lines.

Save a copy to `.tanka/drafts/<date>-<slug>.md` for the audit trail and in case the session is interrupted.

## 3. Verify (required if it goes to someone outside, or the objective asks for it)
Run the `tanka-verifier` subagent with the draft and the original request. On FAIL, apply the minimal changes and show the draft again. Do not go back to the user until you have a PASS or two attempts.

## 4. Confirm
Ask exactly: "Send it as is? (yes / change X / no)". Only a clear yes authorises it. "Fine, but…" is not a yes: apply the change and ask again.

## 5. Send
- Use the send tool of whichever MCP server is available, with the SAME content you showed. The harness will ask the user for one more confirmation; that is expected.
- If the harness rejects the send, read the reason (placeholder, blocked recipient, objective without `may_send`), fix it and go back to step 2. Never reach for a different tool to get around the block.
- If no send tool is available, say plainly that you can only leave the draft, and point to the copy in `.tanka/drafts/` (or a provider draft if that tool exists).

## 6. Close
Report using the real value the tool returned (message id, timestamp):

```
Status: done
Done: sent to … (id …)
Left: nothing
```

If the user said "no" or "later", the status is `needs-confirmation` and the draft stays saved.
