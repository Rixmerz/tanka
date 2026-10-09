# Calendar module

Lets a Tanka assistant read the user's Google Calendar (agenda, an event's details, free time) and create simple events and ask to delete them, only for the Google accounts the user assigned to that assistant.

It drives the Google Calendar web app in the same headless [Rastro](https://github.com/Rixmerz/rastro) session as the [Gmail module](../gmail/README.md), so it reuses the sign-in saved by `tanka gmail login`: no API keys, no OAuth project, and credentials never leave the browser profile.

## Setup

```bash
tanka install calendar <workspace>          # add the calendar skill (4 tools) to a workspace
tanka calendar login                        # same as `tanka gmail login`: sign in to Google in a normal window
tanka calendar status                       # assigned accounts, and whether each one's calendar opens
```

Requirements: Rastro and a Chromium-based browser (see the Gmail module). The Gmail module does not need to be installed in the workspace; only its session and sign-in are shared.

## Risk

The module drives Calendar's web page, not Google's API, so it can break when Google changes the page, and automating a Google account may be against its provider's terms or a company's security policy. Check before using a work account. Creating an event is visible to anyone who can see the calendar and runs without a confirmation prompt from the harness; the skill asks first, and a run with nobody watching (`TANKA_UNATTENDED` without `TANKA_CHAT`) refuses unless the account has `"unattended_write": true` (the page's Workspace tab → Autonomy switches it for the workspace's accounts).

## Which accounts each assistant can use

`accounts.json` in `TANKA_CALENDAR_HOME`, edited by the user only. When that file does not exist, the Gmail module's `accounts.json` is used, so one list can serve both modules.

```json
{
  "accounts": {
    "someone@example.com": {"workspace": "personal"},
    "work.address@example.com": {"workspace": "work", "unattended_write": true}
  }
}
```

An address that is not listed, or that belongs to another workspace, is refused before the browser is touched. Google opens the first signed-in account when asked for one that is not signed in, so every page load also checks the account the page belongs to.

## How it works

- **Agenda**: loads the schedule view (`/calendar/u/0/r/agenda/<y>/<m>/<d>?authuser=<address>`) and reads each event's visible title and its accessible label (time range or "all day", place); the day comes from the day group's key. The last agenda is kept per workspace, so the assistant refers to events by number.
- **Details**: opens the event's own details card from the agenda (read only) and reads title, time, recurrence, place, video link, organizer, guests (count and first ten names) and the description, capped.
- **Free time**: computed from the agenda, inside `TANKA_CALENDAR_WORK_HOURS` on `TANKA_CALENDAR_WORK_DAYS`. Timed events are busy (overlaps merged); all-day and multi-day events are not.
- **Create**: reads that day first and refuses a duplicate (same title starting at the same time) or an overlap (unless `allow_overlap`), then opens Calendar's prefilled event form (`/r/eventedit?text=…&dates=…&location=…&details=…`), checks the title and that there are no guests, saves, and reads the day again to confirm. The tool has no guest parameter, so no invitation is ever sent.
- The session may write only to `calendar.google.com` while a calendar tool runs (enforced by Rastro), and the library opens no other host. It shares the Gmail module's lock, so the two never drive the tab at once.

- **Delete**: `calendar_delete` only asks. It writes a request in `requests/<workspace>.json` (beside `accounts.json`), the chat shows **Delete** and **Keep**, and only the click runs `confirm.py`, which opens the event, checks it is the one asked for, has no guests and does not repeat (Google would then ask whom to tell or which occurrences), deletes it and reads the day again to prove it is gone. Google keeps deleted events in its trash for 30 days. To move an event, create the new one and ask to delete the old one.

There are no tools to edit an event in place, to invite guests, or to answer invitations in this version.

Calendar's interface text is localised; the parsing matches English and Spanish labels and Calendar's stable ids (`data-eventid`, `data-datekey`, `#xDetDlg…`, `#xTiIn`, `#xSaveBu`). Another interface language may need its labels added in `gcal.py`.

## Configuration

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_CALENDAR_HOME` | `~/.tanka/shared/calendar` | Where `accounts.json` and the last agendas live |
| `TANKA_GMAIL_HOME` | `~/.tanka/shared/gmail` | The Gmail module's folder: fallback `accounts.json` and the shared lock |
| `TANKA_CALENDAR_SESSION` | `$TANKA_GMAIL_SESSION`, else `gmail` | The Rastro session (the Gmail module's) |
| `TANKA_CALENDAR_WORK_HOURS` | `09:00-18:00` | Working hours for free time |
| `TANKA_CALENDAR_WORK_DAYS` | `1,2,3,4,5` | Working days, ISO numbers (Monday = 1) |
| `TANKA_CALENDAR_DESC_CHARS` | `1500` | Longest event description shown to the assistant |

## Files

| File | What it does |
| --- | --- |
| `module.json` | Name, description and required programs, read by `tanka modules` |
| `gcal.py` | Scope, the session, parsing, free time, creating (not `calendar.py`, which would hide the standard library's module) |
| `cli.py` | `tanka calendar login\|status`, and `post-install` |
| `skill/` | The skill `tanka install` copies: `SKILL.md` and four tools |

Tests: `tests/test_calendar.py`.
