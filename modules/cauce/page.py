"""cauce on the page (docs/page.md): the Code tab with what needs you, what runs and what waits per
repository; each cauce session with its work; finished and stuck tasks in the chat; and the user's
buttons (cancel, reopen a paused repository, run a repository's queue, send a session a prompt)."""
from __future__ import annotations

import time
from pathlib import Path

import cauce_link as cl

STREAM_HOURS = 24   # how far back a finished or stuck task shows in the chat
DONE_ON_PAGE = 15


def installed(ws: Path) -> bool:
    return (ws / ".claude" / "skills" / "cauce").is_dir()


def state(scope: str, ws: Path) -> dict:
    mine = cl.repos(scope)
    names = sorted(mine)
    base = {"repos": names, "working": [n for n in names if cl.working(scope, n)], "error": None}
    if not mine:
        return dict(base, board=None)
    try:
        b = cl.board(scope)
    except cl.ToolError as e:
        return dict(base, board=None, error=str(e))
    return dict(base, board={k: b[k] for k in ("counts", "needs_you", "running", "answering", "queued")} | {"done": b["done"][:DONE_ON_PAGE]})


def ended(scope: str) -> list[dict]:
    """Tasks that passed or stopped for the user in the last STREAM_HOURS, newest last."""
    if not cl.repos(scope):
        return []
    try:
        b = cl.board(scope)
    except cl.ToolError:
        return []
    since = time.time() - STREAM_HOURS * 3600
    seen, out = set(), []
    for t in b["needs_you"] + b["done"]:
        if t.get("source") not in cl.CAUCE_SOURCES:
            continue  # a prompt answered in the user's own session is not a task cauce ran
        at = cl.epoch(t.get("updated_at"))
        if at < since or t["id"] in seen:
            continue
        seen.add(t["id"])
        out.append({"t": at, "who": "task", "id": t["id"], "title": cl.clip(t.get("title"), cl.TITLE_CHARS),
                    "status": t.get("status"), "repo": t.get("repo_name", ""), "cell": cl.cell_of(t),
                    "asks": t.get("asks", ""), "branch": t.get("branch"), "cost": t.get("cost_usd") or 0})
    return sorted(out, key=lambda x: x["t"])


def stream(scope: str, ws: Path) -> list[dict]:
    return ended(scope)


def context(scope: str, ws: Path, since: float, until: float) -> list[tuple[float, str]]:
    return [(x["t"], f"cauce task #{x['id']} in {x['repo']} ended {x['status']}: {x['title']}"
             + (f" ({x['asks']})" if x["asks"] else "")) for x in ended(scope) if since < x["t"] < until]


def hint(scope: str, ws: Path) -> str:
    return ("The user's coding tasks run in cauce and show in the Code tab of their page. cauce_board lists them, "
            "cauce_task explains one, cauce_memory says what was tried on a problem before, cauce_queue queues a new "
            "one in a repository this workspace may use. cauce_sessions shows each of the user's coding sessions with its "
            "work, and cauce_send sends one a prompt after the user approved the exact text. You cannot "
            "run the queue, cancel or merge: the user does that with the buttons in the Code tab.")


def health() -> list[dict]:
    exe = cl.binary()
    found = cl.version() if exe else None
    return [{"label": "cauce", "ok": bool(found),
             "text": found or ("not installed: claude plugin install cauce@rixmerz" if not exe else f"{exe} does not answer")}]


def task_detail(scope: str, ws: Path, q: dict) -> dict:
    try:
        task_id = int(q.get("id", ""))
    except ValueError as e:
        raise cl.ToolError("a task id is a number") from e
    d = cl.task(scope, task_id)
    keep = ("seq", "cell", "turns", "cost_usd", "passed", "failure", "summary", "move", "move_reason")
    return {"task": d["task"], "branch": d.get("branch"), "plan": d.get("plan"),
            "attempts": [{k: a.get(k) for k in keep} for a in d.get("attempts", [])]}


def project_list(scope: str, ws: Path, q: dict) -> dict:
    return {"projects": cl.projects(scope)}


def session_list(scope: str, ws: Path, q: dict) -> dict:
    name = str(q.get("repo", "") or "")
    return {"sessions": cl.sessions(scope, name or None)}


def problem_list(scope: str, ws: Path, q: dict) -> dict:
    """The page is the user's: it may read cauce's whole memory (all=1), not only this workspace's."""
    return {"problems": cl.problems(scope, str(q.get("q", "") or ""), everywhere=str(q.get("all", "")) == "1")}


def queue_task(scope: str, ws: Path, body: dict) -> dict:
    check = str(body.get("check", "") or "").strip()
    return cl.queue(scope, str(body.get("repo", "")), str(body.get("text", "")), check or None)


def send_prompt(scope: str, ws: Path, body: dict) -> dict:
    """The user's own prompt, typed on the page, into one of this workspace's cauce sessions."""
    msg = cl.send(scope, str(body.get("id", "")), str(body.get("text", "")), "user")
    return {"ok": True, "id": msg["id"], "link": msg["link"]}


def _id(body: dict) -> int:
    try:
        return int(body.get("id", ""))
    except ValueError as e:
        raise cl.ToolError("a task id is a number") from e


ACTIONS = {
    "cancel": lambda scope, ws, body: cl.cancel(scope, _id(body)),
    "unpause": lambda scope, ws, body: cl.unpause(scope, str(body.get("repo", ""))),
    "work": lambda scope, ws, body: cl.work(scope, str(body.get("repo", ""))),
    "queue": queue_task,
    "send": send_prompt,
    "allow": lambda scope, ws, body: {"allowed": cl.allow_known(scope, str(body.get("dir", "")),
                                                                str(body.get("name", "") or "") or None)},
}
GETS = {"task": task_detail, "projects": project_list, "sessions": session_list, "problems": problem_list}
