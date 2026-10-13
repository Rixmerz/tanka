# Google Chat module

Lets a Tanka assistant list, read and send messages in Google Chat, the chat inside Gmail (direct messages and spaces of a Google Workspace account), with files. **Every message waits for your Approve click** in the chat, unless you list that person or conversation as bypass.

It drives `chat.google.com` in the [gmail](../gmail/README.md) module's headless [Rastro](https://github.com/Rixmerz/rastro) session: the same browser, sign-in and lock, so there is no login of its own and no API key.

## Setup

```bash
tanka install gmail <workspace>        # if it is not there yet, and `tanka gmail login`
tanka install approvals <workspace>    # the Approve and Dismiss buttons
tanka install gchat <workspace>        # gchat_read and gchat_send, and an example access.json
tanka gchat status                     # the account each workspace uses and who it may write to without asking
```

## What each assistant may do

- **Which account.** A workspace uses Chat with the one Google account the gmail module's `accounts.json` gives it. Nothing else.
- **Reading.** `gchat_read` lists the recent conversations or shows the last messages of one, with attachments by name. Chat marks a conversation the page opens as seen; nothing that was said changes.
- **Sending.** `gchat_send` sends nothing by default: it leaves the exact text (and the file's name) with the [approvals](../approvals/README.md) module, and the chat shows it with **Approve** and **Dismiss**. Your click runs the skill's `approve.py`, which opens the conversation again and sends that one stored message, once.
- **Bypass.** `access.json`, in `TANKA_GCHAT_HOME`, lists per workspace who it may write to without asking: an email (any conversation that person writes in, including your direct messages with them) or a conversation id (`space/…` or `dm/…`, as `gchat_read` shows it). You edit it; the assistants cannot, because they only write inside their own workspace.

```json
{
  "scopes": {
    "work": {"bypass": ["colleague@example.com", "space/AAAAexample"]}
  }
}
```

A routine or a trigger may still leave a request: nothing is sent until you press Approve.

- **Files.** An attachment must be inside the workspace (a relative path is taken from it) or in `TANKA_GMAIL_UPLOAD_DIRS`, the same rule as mail. Rastro enforces it too: the session only uploads from those folders.

## Risk

The module drives Chat's web page, not Google's API, so it can break when Google changes the page, and automating a work account may be against your company's security policy. Check before using it. A message in a space is seen by everyone in it.

## Configuration

| Variable | Default | What |
|---|---|---|
| `TANKA_GCHAT_HOME` | `~/.tanka/shared/gchat` | `access.json`, the last list per workspace and which `/u/<n>/` serves each account |
| `TANKA_GMAIL_UPLOAD_DIRS` | (none) | More folders attachments may come from, besides the workspace |

## Files

| File | What it does |
| --- | --- |
| `gchat.py` | The account, the bypass list, the page scripts, listing, reading, sending and the request |
| `cli.py` | `tanka gchat status`, and `post-install` |
| `skill/` | The skill `tanka install` copies: `SKILL.md`, two tools and `approve.py` |

Tests: `tests/test_gchat.py`.
