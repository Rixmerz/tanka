"""Tanka's side of tanka-link (the Claude Code plugin in link/): the sessions it registered and their inboxes.

A session is listed while its listener runs (it waits for a message) or ran in the last half hour (it is busy
with a turn and listens again when the turn ends). Sending writes one file in that session's inbox; the
listener hands it to the session and moves it to delivered/."""
from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("TANKA_LINK_HOME", Path.home() / ".tanka" / "shared" / "link"))
BUSY_FOR = 1800          # a session without a listener is still listed this long after it was last seen
TEXT_MAX = 20000
SID_RE = re.compile(r"[A-Za-z0-9-]{8,64}")
SCOPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
SENDERS_RE = re.compile(r"[A-Za-z][A-Za-z0-9 _-]{0,39}")


class ToolError(Exception):
    pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def sessions(now: float | None = None) -> list[dict]:
    """Live sessions, most recently seen first, each with its state: listening or busy."""
    now = now or time.time()
    out = []
    for f in sorted((HOME / "sessions").glob("*.json")) if (HOME / "sessions").is_dir() else []:
        try:
            s = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not SID_RE.fullmatch(str(s.get("id", ""))):
            continue
        try:
            listening = _alive(int((HOME / "sessions" / f"{s['id']}.pid").read_text()))
        except (OSError, ValueError):
            listening = False
        if not listening and now - float(s.get("seen") or 0) > BUSY_FOR:
            continue
        inbox = HOME / "inbox" / s["id"]
        s["state"] = "listening" if listening else "busy"
        s["queued"] = len(list(inbox.glob("*.json"))) if inbox.is_dir() else 0
        out.append(s)
    return sorted(out, key=lambda s: -float(s.get("seen") or 0))


def send(session_id: str, text: str, sender: str) -> dict:
    """Queue one message for a session; the listener delivers it (at once if it is idle)."""
    if not SID_RE.fullmatch(session_id or ""):
        raise ToolError("That is not a session id.")
    text = (text or "").strip()
    if not text or len(text) > TEXT_MAX:
        raise ToolError(f"The message is empty or longer than {TEXT_MAX} characters.")
    if not SENDERS_RE.fullmatch(sender or ""):
        raise ToolError("Bad sender.")
    if not any(s["id"] == session_id for s in sessions()):
        raise ToolError("That session is not open any more (or not listening). List the sessions again.")
    msg = {"id": f"{int(time.time() * 1000)}-{secrets.token_hex(3)}", "from": sender, "text": text, "t": time.time()}
    box = HOME / "inbox" / session_id
    box.mkdir(parents=True, exist_ok=True)
    tmp = box / f".{msg['id']}.tmp"
    tmp.write_text(json.dumps(msg, ensure_ascii=False), encoding="utf-8")
    tmp.replace(box / f"{msg['id']}.json")
    return msg


def delivered(session_id: str, limit: int = 10) -> list[dict]:
    box = HOME / "delivered" / session_id
    items = []
    for f in sorted(box.glob("*.json"))[-limit:] if box.is_dir() else []:
        try:
            items.append(json.loads(f.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return items


def label(s: dict) -> str:
    return f"{s.get('project') or '?'}{' (' + s['branch'] + ')' if s.get('branch') else ''}"


# ---------------------------------------------------------------- the assistant's tools

def listing_file(scope: str) -> Path:
    if not SCOPE_RE.fullmatch(scope or ""):
        raise ToolError(f"'{scope}' is not a workspace scope.")
    return HOME / f"listing-{scope}.json"


def sessions_tool(scope: str) -> None:
    live = sessions()
    listing_file(scope).parent.mkdir(parents=True, exist_ok=True)
    listing_file(scope).write_text(json.dumps({str(i): s["id"] for i, s in enumerate(live, 1)}), encoding="utf-8")
    if not live:
        print("No Claude Code session is open with tanka-link. The user opens them; you cannot.")
        return
    print(f"{len(live)} open Claude Code session(s), most recent first (number | project (branch) | state | folder):")
    for i, s in enumerate(live, 1):
        q = f", {s['queued']} message(s) waiting" if s["queued"] else ""
        print(f"{i} | {label(s)} | {s['state']}{q} | {s['cwd']}")
    print("To send one a prompt: link_send with its number, only after the user approved the exact text.")


def send_tool(scope: str, number: int, text: str, sender: str) -> None:
    f = listing_file(scope)
    ids = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    sid = ids.get(str(number))
    if not sid:
        raise ToolError(f"There is no session number {number} in the last list. Call link_sessions first.")
    msg = send(sid, text, sender)
    s = next((x for x in sessions() if x["id"] == sid), {})
    when = "now (it was waiting)" if s.get("state") == "listening" else "when its current turn ends"
    print(f"Sent to {label(s)}: it receives it {when}. Message {msg['id']}. Do not send it again.")


def run(main) -> None:
    try:
        main(json.load(sys.stdin))
    except ToolError as e:
        sys.exit(str(e))
