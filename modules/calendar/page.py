"""Calendar on the page (docs/page.md): when the assistant asks to delete an event, the chat shows it with Delete and
Keep. The assistant never deletes: the tool only leaves a request, and only these buttons, which the user clicks, act."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import gcal as g
from tanka_kit import ToolError

HERE = Path(__file__).resolve().parent


def installed(ws: Path) -> bool:
    return (ws / ".claude" / "skills" / "calendar").is_dir()


UNATTENDED_LABEL = "Routines and triggers may add events to this workspace's calendars"


def _mine(data: dict, scope: str) -> list[dict]:
    return [v for v in (data.get("accounts") or {}).values() if isinstance(v, dict) and v.get("workspace") == scope]


def state(scope: str, ws: Path) -> dict:
    try:
        mine = _mine(json.loads(g.registry_path().read_text(encoding="utf-8")), scope)
    except (OSError, ValueError):
        mine = []
    autonomy = [{"key": "unattended_write", "label": UNATTENDED_LABEL,
                 "on": all(v.get("unattended_write") is True for v in mine)}] if mine else []
    return {"requests": g.read_requests(scope)[:12], "autonomy": autonomy}


def _autonomy(scope: str, ws: Path, b: dict) -> dict:
    """The user's switch, kept on each of this workspace's accounts in the account list."""
    if b.get("key") != "unattended_write":
        raise ToolError("Unknown switch.")
    path = g.registry_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    mine = _mine(data, scope)
    if not mine:
        raise ToolError("No calendar is assigned to this workspace.")
    for v in mine:
        v["unattended_write"] = b.get("on") is True
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return {"ok": True}


def effects(scope: str, ws: Path, spans: list[tuple[float, float]]) -> list[list[dict]]:
    """For each answer's span: the deletions the assistant asked for in it."""
    items = g.read_requests(scope)
    return [[{"type": "event-delete", "id": r["id"], "text": r["event"]["title"], "when": f"{r['event']['date']} {g.when(r['event'])}",
              "status": r.get("status")} for r in items if t0 < r.get("t", 0) <= t1] for t0, t1 in spans]


def hint(scope: str, ws: Path) -> str:
    return ("You cannot delete a calendar event yourself: calendar_delete only asks, and the user presses Delete in the chat. "
            "Call it only for an event the user asked to remove, then say it waits for their click.")


def _act(what: str, scope: str, body: dict) -> dict:
    p = subprocess.run([sys.executable, str(HERE / "confirm.py"), what, scope, str(body.get("id", ""))],
                       capture_output=True, text=True, timeout=240)
    if p.returncode:
        raise ToolError((p.stderr or "It failed.").strip()[-300:])
    return {"ok": True, "message": p.stdout.strip()}


ACTIONS = {
    "delete": lambda scope, ws, b: _act("delete", scope, b),
    "dismiss": lambda scope, ws, b: _act("dismiss", scope, b),
    "autonomy": _autonomy,
}
