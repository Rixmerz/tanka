# Gmail module

Lets a Tanka assistant read, search, reply to and send Gmail, and download and attach documents, only for the mailboxes the user assigned to that assistant. One browser session can hold several Google accounts; each workspace sees only its own.

It drives Gmail's web app in a headless [Rastro](https://github.com/Rixmerz/rastro) session: no API keys, no app passwords, and credentials never leave the browser profile.

## Setup

```bash
tanka install gmail <workspace>             # add the gmail skill (4 tools) to a workspace
tanka gmail login                           # a normal browser window: sign in to one or more Google accounts
tanka gmail status                          # signed-in accounts and which workspace reads each
```

`tanka install` copies [`skill/`](skill/), scoped to the workspace's name (`--scope NAME` to pick another); this module's `post-install` creates an example `accounts.json` if there is none. `login` opens a visible window because Google's sign-in needs one; the tools then run the session headless through [`../common/chromium-headless`](../common/chromium-headless).

Requirements: Rastro and a Chromium-based browser.

## Risk

The module drives Gmail's web page, not Google's API, so it can break when Google changes the page, and automating a Google account may be against the terms of the account's provider or against a company's security policy. Check before using a work account. Google refuses to sign in inside a browser that automation tools drive, so `tanka gmail login` opens a **normal** window on the session's profile and `tanka` only automates it afterwards, once the sign-in is saved. That window runs with the same fake keychain as the automated browser (`--use-mock-keychain`); otherwise macOS would encrypt the cookies with its real keychain and the headless tools could not read them. Sending and replying are irreversible and run without a confirmation prompt from the harness; the skill asks for a yes first, and a run with nobody watching refuses unless the mailbox has `auto_reply`.

## Which mailboxes each assistant can use

`accounts.json`, in `TANKA_GMAIL_HOME`, is edited by the user only. It sits outside every workspace, where no assistant can write.

```json
{
  "accounts": {
    "someone@example.com": {"workspace": "personal"},
    "work.address@example.com": {"workspace": "work"}
  }
}
```

An address that is not listed, or that belongs to another workspace, is refused before the browser is touched. When a workspace has more than one mailbox the tools take an `account` argument; with one, it is used by default.

## How it works

- **Listing and search** load Gmail's own list (`#inbox`, or `#search/<query>` with Gmail's search syntax) and read the visible rows. The last list is kept per workspace so the assistant refers to messages by number.
- **Reading** fetches the message's complete original source from inside the logged-in page ("Download original") and parses it with Python's `email` package. Every attachment and every picture pasted in the body is saved to `<workspace>/gmail/<mailbox>/…`; `.xlsx` and `.docx` also get a `.txt` copy. Nothing is marked as read.
- **Replying** opens the conversation from its list row and uses Gmail's Reply, so the answer stays in the thread. **Sending** uses Gmail's compose window. Both type the text through the editor, attach through Gmail's own file input, and wait for Gmail's "sent" notice; `gmail_send` checks Sent first and refuses to repeat the same subject to the same person within an hour.
- **Archiving** (`gmail_archive`) opens the conversation and presses Gmail's Archive, then searches the inbox to confirm it left. **The trash** (`gmail_trash`) only asks: it writes a request in `requests/<workspace>.json` (beside `accounts.json`), the chat shows **Delete** and **Keep**, and only the click runs `confirm.py`, which presses Gmail's Delete (the trash keeps it 30 days).
- The session may write only to `mail.google.com` and upload only from the workspace and `TANKA_GMAIL_UPLOAD_DIRS`. Both limits are enforced by Rastro, so a message cannot talk the assistant into attaching an arbitrary file from the disk. The library checks the path first as well.

Gmail's interface text is localised; the code matches English and Spanish labels and Gmail's stable class names. Another interface language may need its labels added in `gmail.py`.

## Configuration

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_GMAIL_HOME` | `~/.tanka/shared/gmail` | Where `accounts.json`, the lock and the last lists live |
| `TANKA_GMAIL_SESSION` | `gmail` | The Rastro session name |
| `TANKA_GMAIL_PROFILE` | `$RASTRO_HOME/profiles/<session>` | The browser profile the sign-in is saved in |
| `TANKA_GMAIL_ATTACH_DIR` | `gmail` | Downloaded attachments, relative to the workspace |
| `TANKA_GMAIL_UPLOAD_DIRS` | (none) | Extra folders attachments may be taken from, `:`-separated |
| `TANKA_GMAIL_MAX_MB` | `25` | Largest message or attachment handled |
| `TANKA_GMAIL_BODY_CHARS` | `4000` | Longest body shown to the assistant |
| `TANKA_BROWSER` | first found: Chromium, Chrome, Brave, Edge | The browser behind the headless wrapper |

## Files

| File | What it does |
| --- | --- |
| `module.json` | Name, description and required programs, read by `tanka modules` |
| `gmail.py` | Scope, the session, listing, reading and parsing, replying, sending |
| `cli.py` | `tanka gmail login\|status`, and `post-install` |
| `skill/` | The skill `tanka install` copies: `SKILL.md` and four tools |

Tests: `tests/test_gmail.py`.
