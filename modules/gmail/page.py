"""Gmail on the page (docs/page.md): when the assistant asks to move a conversation to the trash, the chat shows it with Delete and
Keep. The assistant never deletes: the tool only leaves a request, and only these buttons, which the user clicks, act."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import gmail as g
from tanka_kit import ToolError

HERE = Path(__file__).resolve().parent


def installed(ws: Path) -> bool:
    return (ws / ".claude" / "skills" / "gmail").is_dir()


def state(scope: str, ws: Path) -> dict:
    return {"requests": g.read_requests(scope)[:12]}


def effects(scope: str, ws: Path, spans: list[tuple[float, float]]) -> list[list[dict]]:
    """For each answer's span: the conversations the assistant asked to move to the trash in it."""
    items = g.read_requests(scope)
    return [[{"type": "mail-trash", "id": r["id"], "text": r.get("subject", ""), "when": r.get("address", ""),
              "status": r.get("status")} for r in items if t0 < r.get("t", 0) <= t1] for t0, t1 in spans]


def hint(scope: str, ws: Path) -> str:
    return ("You cannot move mail to the trash yourself: gmail_trash only asks, and the user presses Delete in the chat. "
            "To clear the inbox, gmail_archive is enough and needs no click.")


def _act(what: str, scope: str, body: dict) -> dict:
    p = subprocess.run([sys.executable, str(HERE / "confirm.py"), what, scope, str(body.get("id", ""))],
                       capture_output=True, text=True, timeout=240)
    if p.returncode:
        raise ToolError((p.stderr or "It failed.").strip()[-300:])
    return {"ok": True, "message": p.stdout.strip()}


ACTIONS = {
    "delete": lambda scope, ws, b: _act("delete", scope, b),
    "dismiss": lambda scope, ws, b: _act("dismiss", scope, b),
}
