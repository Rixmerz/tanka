"""Approvals: a workspace skill asks, the user approves on the page, and only that click runs the action.

A tool that must never act on its own (open a ticket, publish, pay) calls `ask()` with the exact text the user will
read and the payload the action needs. Nothing happens then. The page shows the request under the answer with
Approve and Dismiss; Approve runs `<workspace>/.claude/skills/<skill>/approve.py` once, with the stored payload on
stdin. The requests live in ~/.tanka/shared/approvals/<scope>.json, outside every workspace.

Standard library only."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import secrets
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("TANKA_APPROVALS_HOME", Path.home() / ".tanka" / "shared" / "approvals"))
TIMEOUT = int(os.environ.get("TANKA_APPROVALS_TIMEOUT", "180"))
SCOPE_RE = re.compile(r"[A-Za-z0-9_-]+")
SKILL_RE = re.compile(r"[a-z][a-z0-9_-]*")
MAX_KEPT = 30  # closed requests kept for the page; pending ones are always kept
MAX_TEXT = 6000
MAX_RESULT = 600


class ToolError(Exception):
    pass


def requests_file(scope: str) -> Path:
    if not SCOPE_RE.fullmatch(scope or ""):
        raise ToolError(f"'{scope}' is not a workspace scope. Tell the user.")
    return HOME / f"{scope}.json"


@contextlib.contextmanager
def locked(scope: str):
    f = requests_file(scope)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f.with_suffix(".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read(scope: str) -> list[dict]:
    try:
        data = json.loads(requests_file(scope).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("approvals") if isinstance(data, dict) else None
    return [a for a in items if isinstance(a, dict) and a.get("id")] if isinstance(items, list) else []


def write(scope: str, items: list[dict]) -> None:
    f = requests_file(scope)
    f.parent.mkdir(parents=True, exist_ok=True)
    pending = [a for a in items if a.get("status") == "pending"]
    rest = [a for a in items if a.get("status") != "pending"][:MAX_KEPT]
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"approvals": pending + rest}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(f)


def ask(scope: str, skill: str, title: str, text: str, payload: dict) -> dict:
    """Leave a request for the user. Nothing runs here, ever."""
    title, text = " ".join(str(title).split())[:200], str(text).strip()[:MAX_TEXT]
    if not SKILL_RE.fullmatch(skill or ""):
        raise ToolError(f"'{skill}' is not a skill name.")
    if not title or not isinstance(payload, dict):
        raise ToolError("A request needs a title and a payload.")
    with locked(scope):
        items = read(scope)
        if any(a.get("skill") == skill and a.get("title") == title and a.get("status") == "pending" for a in items):
            raise ToolError("The same request is already waiting for the user. Tell them to approve it on the page.")
        a = {"id": "a-" + secrets.token_hex(4), "skill": skill, "title": title, "text": text, "payload": payload,
             "t": round(time.time(), 3), "status": "pending"}
        write(scope, [a] + items)
    return a


def _find(items: list[dict], aid: str) -> dict:
    for a in items:
        if a["id"] == aid:
            return a
    raise ToolError("That request does not exist any more.")


def _close(scope: str, aid: str, status: str, result: str = "") -> None:
    with locked(scope):
        items = read(scope)
        a = _find(items, aid)
        a["status"], a["closed"] = status, round(time.time(), 3)
        if result:
            a["result"] = result[-MAX_RESULT:]
        write(scope, items)


def approve(scope: str, ws: Path, aid: str) -> str:
    """The user's click: run the skill's approve.py once with the stored payload."""
    with locked(scope):
        items = read(scope)
        a = _find(items, aid)
        if a.get("status") != "pending":
            raise ToolError(f"This request is already {a.get('status')}.")
        script = ws / ".claude" / "skills" / a["skill"] / "approve.py"
        if not SKILL_RE.fullmatch(a.get("skill", "")) or not script.is_file():
            raise ToolError(f"The skill '{a.get('skill')}' has no approve.py in this workspace.")
        a["status"] = "running"  # a second click, or a second page, cannot run it again
        write(scope, items)
    try:
        p = subprocess.run([sys.executable, str(script)], input=json.dumps({"id": aid, "payload": a["payload"]}),
                           capture_output=True, text=True, timeout=TIMEOUT, cwd=str(ws))
    except subprocess.TimeoutExpired:
        _close(scope, aid, "failed", "It took too long; check by hand whether it happened before asking again.")
        raise ToolError("It took too long. Check by hand whether it happened before asking again.")
    if p.returncode:
        why = (p.stderr or p.stdout or "It failed.").strip()
        _close(scope, aid, "failed", why)
        raise ToolError(why[-300:])
    out = p.stdout.strip()
    _close(scope, aid, "approved", out)
    return out


def dismiss(scope: str, aid: str) -> str:
    with locked(scope):
        items = read(scope)
        a = _find(items, aid)
        if a.get("status") != "pending":
            raise ToolError(f"This request is already {a.get('status')}.")
        a["status"], a["closed"] = "dismissed", round(time.time(), 3)
        write(scope, items)
    return "Dismissed; nothing was done."


def list_tool(scope: str) -> None:
    items = read(scope)[:15]
    if not items:
        print("No requests.")
    for a in items:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(a.get("t", 0)))
        line = f"{a['id']} [{a.get('status')}] {when} {a.get('skill')}: {a.get('title')}"
        if a.get("result"):
            line += " -> " + " ".join(str(a["result"]).split())[:300]
        print(line)


def run(main) -> None:
    try:
        main(json.load(sys.stdin))
    except ToolError as e:
        sys.exit(str(e))


def ask_tool(scope: str, skill: str, title: str, text: str, payload: dict) -> str:
    """What a tool prints after asking."""
    a = ask(scope, skill, title, text, payload)
    return (f"Request {a['id']} saved: \"{a['title']}\". NOTHING was done: the user must press Approve in the chat. "
            "Tell them, and do not say it is done until they approve.")
