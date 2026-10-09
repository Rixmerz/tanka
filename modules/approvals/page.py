"""Approvals on the page (docs/page.md): when a workspace skill asks to do something, the chat shows the exact text
with Approve and Dismiss. The assistant never runs it: its tool only leaves a request, and only the user's click acts."""
from __future__ import annotations

from pathlib import Path

import approvals as ap
from tanka_kit import ToolError


def installed(ws: Path) -> bool:
    return (ws / ".claude" / "skills" / "approvals").is_dir()


def state(scope: str, ws: Path) -> dict:
    return {"approvals": [{k: a.get(k) for k in ("id", "skill", "title", "text", "status", "result", "t")}
                          for a in ap.read(scope)[:12]]}


def effects(scope: str, ws: Path, spans: list[tuple[float, float]]) -> list[list[dict]]:
    """For each answer's span: the approvals the assistant asked for in it."""
    items = ap.read(scope)
    return [[{"type": "approval", "id": a["id"], "title": a.get("title", ""), "status": a.get("status")}
             for a in items if t0 < a.get("t", 0) <= t1] for t0, t1 in spans]


def hint(scope: str, ws: Path) -> str:
    return ("Some of your tools only ask: they leave a request with the exact text, and the user presses Approve in "
            "the chat. After one of them, say it waits for their click and never say it is done before that.")


def _act(fn, scope: str, ws: Path, body: dict) -> dict:
    try:
        return {"ok": True, "message": fn(scope, ws, str(body.get("id", "")))}
    except ap.ToolError as e:
        raise ToolError(str(e))


ACTIONS = {
    "approve": lambda scope, ws, b: _act(ap.approve, scope, ws, b),
    "dismiss": lambda scope, ws, b: _act(lambda s, _w, i: ap.dismiss(s, i), scope, ws, b),
}
