"""Boards on the page (docs/page.md): a tab with each view's rows grouped and counted, chips under an
answer for the rows it recorded, and the user's own changes (a status, archiving a row)."""
from __future__ import annotations

from pathlib import Path

import boards as b


def installed(ws: Path) -> bool:
    return b.installed(ws)


def state(scope: str, ws: Path) -> dict:
    """The views and how many rows each has; the rows come with GET rows, for the view on screen."""
    out = []
    for name, spec in b.load_views(ws).items():
        problems = b.view_problems(name, spec)
        view = {"name": name, "title": spec.get("title") or name, "problems": problems}
        if not problems:
            fields = b.fields_of(spec)
            view.update(description=spec["description"], key=spec["key"], group_by=spec.get("group_by"),
                        columns=spec.get("columns") or [k for k in fields if k != spec.get("group_by")],
                        fields=fields, actions=spec.get("actions", []), count=len(b.live_rows(scope, name)))
        out.append(view)
    return {"views": out}


def rows(scope: str, ws: Path, q: dict) -> dict:
    view = str(q.get("view", ""))
    b.view_spec(scope, view)
    return {"view": view, "rows": b.live_rows(scope, view)}


def effects(scope: str, ws: Path, spans: list[tuple[float, float]]) -> list[list[dict]]:
    """For each answer's span: the rows the assistant recorded or changed."""
    out = [[] for _ in spans]
    for name, spec in b.load_views(ws).items():
        if b.view_problems(name, spec):
            continue
        for r in b.live_rows(scope, name):
            if r.get("updated_by") != "assistant":
                continue
            for i, (t0, t1) in enumerate(spans):
                if t0 < r.get("updated", 0) <= t1:
                    out[i].append({"view": name, "title": spec["title"], "id": r["id"], "label": b.label_of(spec, r),
                                   "type": "created" if r.get("t") == r.get("updated") else "updated",
                                   "changed": r.get("changed", [])})
    return out


def hint(scope: str, ws: Path) -> str:
    return ("The user's boards show on their page; boards_rows lists them and their rows, and each board has its own "
            "tool to record a row (boards_record_<board>). A message that names one row of a board comes from a button "
            "the user pressed on it: do it for that row and record the result on the board.")


ACTIONS = {
    "set": lambda scope, ws, body: b.set_field(scope, str(body.get("view", "")), str(body.get("id", "")),
                                               str(body.get("field", "")), str(body.get("value", ""))),
    "archive": lambda scope, ws, body: b.archive(scope, str(body.get("view", "")), str(body.get("id", ""))),
}
GETS = {"rows": rows}
