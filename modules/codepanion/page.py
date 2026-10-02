"""The codepanion on the page (docs/page.md): its notes and 👍/👎, the watched sessions with their
timelines and signals, the lenses with their check status, and whether the tap runs."""
from __future__ import annotations

import json
import time
from pathlib import Path

import codepanion as c

MARKER = "modules/codepanion/tap.py"
QUOTE_CHARS = 300


def installed(ws: Path) -> bool:
    return c.config_file(ws).is_file()


def session_summary(sid: str, evs: list[dict]) -> dict:
    tools = [e for e in evs if e.get("e") == "tool"]
    start = next((e for e in evs if e.get("e") == "session_start"), evs[0])
    first_prompt = next((e.get("text", "") for e in evs if e.get("e") == "prompt"), "")
    return {"id": sid, "project": evs[0].get("project"), "branch": start.get("branch") or "", "start": evs[0].get("t"),
            "last": evs[-1].get("t"), "prompts": sum(e.get("e") == "prompt" for e in evs), "tools": len(tools),
            "failures": sum(not e.get("ok") for e in tools), "status": c.status_of(evs), "title": first_prompt[:120]}


def notes(scope: str) -> list[dict]:
    verdict = c.verdicts()
    return [dict(n, verdict=verdict.get(n["id"])) for n in reversed(c.read_notes(scope))]


def state(scope: str, ws: Path) -> dict:
    out = {"projects": c.projects_of(scope)}
    try:
        problems, warnings = c.check(ws)
        cfg = c.load_config(ws)
    except c.ToolError as e:
        return dict(out, problems=[str(e)], warnings=[], lenses=[], notes=[], sessions=[])
    mine = notes(scope)
    lenses = []
    for name, lens in c.load_lenses(ws).items():
        said = [n for n in mine if n.get("lens") == name]
        lenses.append({"name": name, "active": name in cfg["lenses"], "meta": lens["meta"], "sections": lens["sections"],
                       "problems": c.lens_problems(name, lens), "notes": len(said),
                       "good": sum(n["verdict"] == "good" for n in said), "bad": sum(n["verdict"] == "bad" for n in said)})
    sessions = [session_summary(sid, evs) for sid, evs in c.sessions_of(scope).items()]
    return dict(out, problems=problems, warnings=warnings, config=cfg, lenses=lenses, notes=mine, sessions=sessions)


def stream(scope: str, ws: Path) -> list[dict]:
    """Its notes and its tuning proposals, in the chat."""
    out = [{"t": n["t"], "who": "note", "id": n["id"], "lens": n.get("lens"), "text": n.get("text", ""),
            "evidence": n.get("evidence", ""), "verdict": n.get("verdict"), "seen": n.get("seen")} for n in notes(scope)]
    out += [{"t": r["t"], "who": "tune", "id": r["id"], "lens": r["lens"], "state": r.get("state", "open"),
             "tunable": r.get("tunable", True), "done": r.get("done", "")} for r in c.suggestions(scope)]
    return out


def quote(text: str) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= QUOTE_CHARS else text[:QUOTE_CHARS - 1] + "…"


def context(scope: str, ws: Path, since: float, until: float) -> list[tuple[float, str]]:
    return [(n["t"], f"note {n['id']} from the lens {n.get('lens')}: {quote(n.get('text', ''))}")
            for n in c.read_notes(scope) if since < n["t"] < until]


def session_detail(scope: str, ws: Path, q: dict) -> dict:
    sid, evs = c.session_of(scope, str(q.get("id", "")))
    th = c.load_config(ws)["thresholds"]
    fires = c.firings(evs, th) + c.idle_firings(evs, th, until=time.time())
    keep = ("t", "e", "text", "tool", "target", "ok", "error", "lines", "branch", "source", "reason")
    return {"summary": session_summary(sid, evs), "events": [{k: e[k] for k in keep if k in e} for e in evs[-400:]],
            "offset": max(0, len(evs) - 400),
            "firings": [{k: f[k] for k in ("signal", "i", "t", "evidence")} for f in fires]}


def tapped() -> list[str]:
    settings = c.CLAUDE_HOME / "settings.json"
    try:
        hooks = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {}) if settings.is_file() else {}
    except json.JSONDecodeError:
        hooks = {}
    return sorted(ev for ev, entries in hooks.items()
                  if any(MARKER in str(h.get("command", "")) for e in entries if isinstance(e, dict)
                         for h in e.get("hooks", []) if isinstance(h, dict)))


def health() -> list[dict]:
    tap, watch = tapped(), c.watch()
    if not watch and not tap:
        return []
    return [{"label": "Codepanion tap", "ok": len(tap) == 6,
             "text": "installed (6 hooks)" if len(tap) == 6 else ("partial: " + ", ".join(tap) if tap else "not installed: tanka codepanion tap install")},
            {"label": "Watched", "ok": bool(watch) or None,
             "text": "; ".join(f"{p} → {w}" for p, w in watch.items()) or "nothing: tanka codepanion watch add <path> <workspace>"},
            {"label": "New notes", "ok": None, "text": str(c.unseen())}]


ACTIONS = {
    "rate": lambda scope, ws, b: {"note": c.rate(str(b.get("id", "")), str(b.get("verdict", "")))},
    "seen": lambda scope, ws, b: {"seen": len(c.mark_seen(scope))},
    "lens": lambda scope, ws, b: {"done": c.lens_action(scope, str(b.get("lens", "")), str(b.get("action", "")))},
}
GETS = {"session": session_detail}
