"""Link on the page (docs/page.md): the Sessions tab lists the Claude Code sessions open with tanka-link, and its
Send button puts the user's own prompt into one. The assistant's link_send asks the user first; this is the user."""
from __future__ import annotations

from pathlib import Path

import link as lk
from tanka_kit import ToolError


def installed(ws: Path) -> bool:
    return (ws / ".claude" / "skills" / "link").is_dir()


def state(scope: str, ws: Path) -> dict:
    out = []
    for s in lk.sessions()[:20]:
        out.append({k: s.get(k) for k in ("id", "project", "branch", "cwd", "state", "queued", "started", "seen")}
                   | {"recent": [{k: m.get(k) for k in ("from", "text", "t", "delivered")} for m in lk.delivered(s["id"], 3)]})
    return {"sessions": out}


def hint(scope: str, ws: Path) -> str:
    return ("The user's Claude Code sessions are reachable with link_sessions and link_send. A prompt you send is acted on "
            "by that session with its own permissions, so show the exact text and the session and wait for a yes.")


def _send(scope: str, ws: Path, body: dict) -> dict:
    try:
        msg = lk.send(str(body.get("id", "")), str(body.get("text", "")), "user")
    except lk.ToolError as e:
        raise ToolError(str(e))
    return {"ok": True, "message": "Sent.", "id": msg["id"]}


ACTIONS = {"send": _send}
