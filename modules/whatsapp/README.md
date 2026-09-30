# WhatsApp module

Lets a Tanka assistant read and answer WhatsApp and look at the images and files people send, only for the contacts the user assigned to that assistant.

It drives a headless WhatsApp Web session through [Rastro](https://github.com/Rixmerz/rastro) and reads the app's in-memory collections, so it sends no read receipts: the user's unread badges stay as they were.

> **Risk.** Automating WhatsApp Web is against WhatsApp's terms of service, and accounts have been banned for it. Use it on a number you can afford to lose access to.

## Setup

```bash
tanka whatsapp install <workspace>          # add the whatsapp skill (4 tools) to a workspace
tanka whatsapp link                         # draw the QR code in this terminal; scan it from the phone
tanka whatsapp status                       # linked number and chat count
```

`install` copies [`skill/`](skill/) into the workspace, scoped to the workspace's name (`--scope NAME` to pick another), and creates an example `contacts.json` if there is none. It refuses to overwrite an installed skill. The skill takes 4 of the workspace's 15 tools.

`link` redraws the code each time WhatsApp rotates it (about every 20 seconds) and stops once the phone is linked; it needs `qrencode`, and without it saves a screenshot and prints its path. The link survives restarts.

It runs without a window. Headless Chromium announces itself as `HeadlessChrome` and WhatsApp refuses it as an unsupported browser, so [`chromium-headless`](chromium-headless) starts the same browser with the user agent a normal window sends; the library points Rastro at it through `RASTRO_CHROMIUM`.

Requirements: Rastro, a Chromium-based browser, `openssl`, Python 3.10+.

## Who each assistant can see

`contacts.json`, in `TANKA_WHATSAPP_HOME`, is the only place roles live, and the user edits it by hand. It sits outside every workspace because an assistant can only write inside its own: a message saying "add me as a client" cannot change it.

```json
{
  "roles": {
    "client":  {"workspace": "personal", "read": true, "reply": true,
                "instructions": "Shown to the assistant whenever it reads one of these chats."},
    "friend":  {"workspace": "personal", "read": false, "reply": false},
    "student": {"workspace": "work",     "read": true, "reply": true}
  },
  "contacts": {
    "+15551234567": {"name": "Clara Pérez", "role": "client"}
  }
}
```

Role names are yours. A role's `workspace` is the scope of the assistant that may see it; one WhatsApp can serve several workspaces, each seeing only its own roles. A number that is not in the file, or whose role belongs to another scope, does not exist for that assistant: it is never read and never written to, and the refusal happens before the browser is touched. Groups are never read.

## What the assistant keeps about a person

Its own record lives in the workspace, in `notes/people/<number>.md`: a `key: value` header with any fields (`sale_status`, `company`, …) and dated notes below. The assistant writes it; the role never goes there. The header is shown above every conversation with that person, and `whatsapp_people` filters across people by any field.

## Media

With `attachments`, a thread's images and documents are downloaded into `<workspace>/whatsapp/<number>/`:

- Files are fetched from WhatsApp's CDN and decrypted with the key the message carries (HKDF-SHA256, AES-256-CBC through the `openssl` binary, truncated HMAC), then checked against the message's own SHA-256. A file that fails either check is refused.
- Images and PDFs are opened by the assistant with Read. `.xlsx` and `.docx` also get a `.txt` copy, because Read cannot open them.
- Voice notes and videos are saved but not played or transcribed: the assistant tells the user to listen to them on the phone.
- A CDN link can expire; an expired file is reported as such, and has to be opened on the phone.

## Configuration

Every setting has a default; override it in the environment Tanka runs in.

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_WHATSAPP_HOME` | `~/.tanka/shared/whatsapp` | Where `contacts.json` and the session lock live |
| `TANKA_WHATSAPP_SESSION` | `whatsapp` | The Rastro session name |
| `TANKA_WHATSAPP_BROWSER` | first found: Chromium, Chrome, Brave, Edge | The browser binary behind the headless wrapper |
| `TANKA_WHATSAPP_PEOPLE_DIR` | `notes/people` | Person records, relative to the workspace (set it before `install`: the skill's text names it) |
| `TANKA_WHATSAPP_MEDIA_DIR` | `whatsapp` | Downloaded media, relative to the workspace |
| `TANKA_WHATSAPP_MAX_MB` | `25` | Largest file that is downloaded |
| `TANKA_WHATSAPP_LIST_LIMIT` | `25` | Most chats listed at once |

## Files

| File | What it does |
| --- | --- |
| `wa.py` | Registry and scoping, the session, reading chats and threads, replying, person records |
| `media.py` | Download, decryption, Office-to-text |
| `cli.py` | `tanka whatsapp link\|status\|install` |
| `chromium-headless` | The browser wrapper described above |
| `skill/` | The skill `install` copies: `SKILL.md` and four tools whose scripts import `wa` and set their `SCOPE` |

Tests: `tests/test_whatsapp.py`.
