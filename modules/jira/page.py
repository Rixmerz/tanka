"""Jira on the page (docs/page.md): when the assistant asks to delete an issue, the chat shows it with Delete and
Keep. The assistant never deletes: the tool only leaves a request, and only these buttons, which the user clicks, act."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import jira as j
from tanka_kit import ToolError

HERE = Path(__file__).resolve().parent


def installed(ws: Path) -> bool:
    return (ws / ".claude" / "skills" / "jira").is_dir()


UNATTENDED_LABEL = "Routines and triggers may change Jira in this workspace's projects"


def _scope_entry(scope: str) -> dict | None:
    try:
        entry = j.scopes().get(scope)
    except j.ToolError:
        return None
    return entry if isinstance(entry, dict) else None


def state(scope: str, ws: Path) -> dict:
    entry = _scope_entry(scope)
    autonomy = [{"key": "unattended_write", "label": UNATTENDED_LABEL, "on": entry.get("unattended_write") is True}] \
        if entry and entry.get("write") is True else []
    return {"deletions": j.read_deletions(scope)[:12], "autonomy": autonomy}


def _autonomy(scope: str, ws: Path, b: dict) -> dict:
    """The user's switch, kept in scopes.json beside the other keys of this workspace's entry."""
    if b.get("key") != "unattended_write":
        raise ToolError("Unknown switch.")
    data = json.loads(j.SCOPES.read_text(encoding="utf-8"))
    entry = (data.get("scopes") or {}).get(scope)
    if not isinstance(entry, dict) or entry.get("write") is not True:
        raise ToolError("This workspace may not write in Jira at all; that is set in scopes.json.")
    entry["unattended_write"] = b.get("on") is True
    tmp = j.SCOPES.with_name(f".{j.SCOPES.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(j.SCOPES)
    return {"ok": True}


def effects(scope: str, ws: Path, spans: list[tuple[float, float]]) -> list[list[dict]]:
    """For each answer's span: the deletions the assistant asked for in it."""
    items = j.read_deletions(scope)
    return [[{"type": "deletion", "id": d["id"], "key": d["key"], "text": d.get("summary", ""), "status": d.get("status")}
             for d in items if t0 < d.get("t", 0) <= t1] for t0, t1 in spans]


def hint(scope: str, ws: Path) -> str:
    return ("You cannot delete a Jira issue yourself: jira_delete only asks, and the user presses Delete in the chat. "
            "Call it only for an issue the user asked to delete, then say it waits for their click.")


def _act(what: str, scope: str, body: dict) -> dict:
    did = str(body.get("id", ""))
    p = subprocess.run([sys.executable, str(HERE / "confirm.py"), what, scope, did], capture_output=True, text=True, timeout=90)
    if p.returncode:
        raise ToolError((p.stderr or "It failed.").strip()[-300:])
    return {"ok": True, "message": p.stdout.strip()}


ACTIONS = {
    "delete": lambda scope, ws, b: _act("delete", scope, b),
    "dismiss": lambda scope, ws, b: _act("dismiss", scope, b),
    "autonomy": _autonomy,
}
