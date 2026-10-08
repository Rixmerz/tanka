"""Routines on the page (docs/page.md): the routines and triggers that run, the assistant's proposals waiting
for the user, and the buttons that approve, reject or remove. The assistant never approves anything: only
these actions, which the user clicks, do."""
from __future__ import annotations

from pathlib import Path

import routines as r

TOOLS = ("routines_list", "routines_history", "routines_propose", "routines_withdraw")


def installed(ws: Path) -> bool:
    return (ws / ".claude" / "skills" / "routines").is_dir()


def state(scope: str, ws: Path) -> dict:
    return r.state(scope, ws)


def waiting(st: dict) -> int:
    return sum(p.get("status") == "pending" for p in st.get("proposals", []))


def effects(scope: str, ws: Path, spans: list[tuple[float, float]]) -> list[list[dict]]:
    """For each answer's span: the proposals the assistant made in it."""
    props = r.state(scope, ws).get("proposals", [])
    return [[{"type": "proposal", "id": p["id"], "text": p.get("name", ""), "status": p.get("status")}
             for p in props if t0 < p.get("t", 0) <= t1] for t0, t1 in spans]


def hint(scope: str, ws: Path) -> str:
    return ("Routines and triggers run on their own and spend money, so you never create one: when the user wants "
            "something done on a schedule, call routines_propose with a name, how often, the exact task and a budget "
            "per run, and tell them it waits for their approval in the Routines tab. To see what exists, routines_list; "
            "what ran and what it reported, routines_history; to take back a proposal still waiting, routines_withdraw. "
            "Never say a routine is approved or running until routines_list shows it.")


def _act(fn, scope: str, ws: Path, arg: str) -> dict:
    message = fn(scope, ws, arg)
    return {"ok": True, "message": message, "daemon": bool(r.state(scope, ws).get("daemon"))}


ACTIONS = {
    "approve": lambda scope, ws, b: _act(r.approve, scope, ws, str(b.get("id", ""))),
    "reject": lambda scope, ws, b: _act(r.reject, scope, ws, str(b.get("id", ""))),
    "remove": lambda scope, ws, b: _act(r.remove, scope, ws, str(b.get("name", ""))),
}
