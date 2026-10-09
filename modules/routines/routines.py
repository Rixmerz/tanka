"""Routines for Tanka: the assistant proposes a routine, the user approves it.

- **Proposals.** routines_propose stores a draft routine in HOME/proposals/<scope>.json, outside
  every workspace, so the assistant reaches it only through its tools. A proposal does nothing.
- **Approval.** Only the user approves (`tanka routines approve`, or the page's Routines tab). That
  writes the routine into the workspace's `.claude/automations.json`, where the automation daemon
  picks it up. A proposal can never create a trigger or change an existing automation.
- **Limits.** How many automations, how often, how expensive: defaults below, overridden by the
  user in HOME/limits.json (or environment variables), which no assistant can write.

Standard library only.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import secrets
import sys
import time
from pathlib import Path

MODULE = Path(__file__).resolve().parent
REPO = MODULE.parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import tanka_automation as ta  # noqa: E402


class ToolError(Exception):
    """A refusal with one sentence that says what to do next."""


HOME = Path(os.environ.get("TANKA_ROUTINES_HOME", Path.home() / ".tanka" / "shared" / "routines"))

DEFAULT_LIMITS = {"max_automations": 8, "min_interval_seconds": 3600, "max_budget_usd": 0.5,
                  "max_pending": 5, "max_task_chars": 800}
LIMIT_ENV = {"max_automations": "TANKA_ROUTINES_MAX_AUTOMATIONS", "min_interval_seconds": "TANKA_ROUTINES_MIN_INTERVAL",
             "max_budget_usd": "TANKA_ROUTINES_MAX_BUDGET", "max_pending": "TANKA_ROUTINES_MAX_PENDING",
             "max_task_chars": "TANKA_ROUTINES_MAX_TASK_CHARS"}
DEFAULT_BUDGET = 0.25
MAX_KEPT = 20            # proposals kept per scope, newest first
MAX_OUTPUT = 5800        # characters of tool output
WHY_CHARS = 200
SCOPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
NAME_RE = re.compile(r"[a-z][a-z0-9-]{1,31}")
ID_RE = re.compile(r"p-[0-9a-f]{6}")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
LOG_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (routine|trigger) (\S+) \| (.*?)(?: \| exit (-?\d+) \| (.*))?$")
STATUSES = ("pending", "approved", "rejected", "withdrawn")


def now() -> float:
    return time.time()


# ---------------------------------------------------------------- settings

def _valid_limit(key: str, value) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    if key == "max_budget_usd":
        return 0 < value <= 100
    return value > 0 and float(value).is_integer()


def auto_start() -> bool:
    """True when the user chose, on the page or in limits.json ("approval": "auto"), that the assistant's routine
    proposals start without their approval (still inside every limit). The default is to wait for them."""
    try:
        user = json.loads((HOME / "limits.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(user, dict) and user.get("approval") == "auto"


def set_auto_start(on: bool) -> None:
    """The user's switch: keeps every other key of limits.json."""
    f = HOME / "limits.json"
    try:
        user = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, json.JSONDecodeError) as e:
        raise ToolError(f"{f} cannot be read ({e}); fix it by hand first.")
    user = user if isinstance(user, dict) else {}
    user["approval"] = "auto" if on else "manual"
    write_atomic(f, user)


def limits() -> dict:
    """The defaults, then the environment, then HOME/limits.json; an invalid value is ignored."""
    out = dict(DEFAULT_LIMITS)
    for key, var in LIMIT_ENV.items():
        raw = os.environ.get(var)
        if raw:
            try:
                value = float(raw) if key == "max_budget_usd" else int(raw)
            except ValueError:
                continue
            if _valid_limit(key, value):
                out[key] = value
    try:
        user = json.loads((HOME / "limits.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        user = {}
    for key, value in (user.items() if isinstance(user, dict) else ()):
        if key in out and _valid_limit(key, value):
            out[key] = float(value) if key == "max_budget_usd" else int(value)
    return out


def check_scope(scope: str) -> str:
    if not isinstance(scope, str) or not SCOPE_RE.fullmatch(scope):
        raise ToolError(f"'{scope}' is not a workspace scope (lowercase letters, digits, - and _). Tell the user.")
    return scope


def proposals_file(scope: str) -> Path:
    return HOME / "proposals" / f"{check_scope(scope)}.json"


def write_atomic(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


@contextlib.contextmanager
def locked(scope: str):
    """One writer at a time per scope: the tool, the CLI and the page may race."""
    f = proposals_file(scope)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f.with_suffix(".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_proposals(scope: str) -> tuple[list[dict], str | None]:
    """(proposals newest first, a notice when the file was unreadable). A corrupt file counts as empty."""
    f = proposals_file(scope)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], None
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return [], f"{f} is not valid JSON; it was treated as empty and is kept as {f.name}.corrupt when next written."
    items = data.get("proposals") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return [], f"{f} has no list of proposals; it was treated as empty."
    good = [p for p in items if isinstance(p, dict) and isinstance(p.get("id"), str) and p.get("status") in STATUSES]
    return sorted(good, key=lambda p: -float(p.get("t") or 0)), None


def write_proposals(scope: str, items: list[dict]) -> None:
    f = proposals_file(scope)
    if f.is_file() and read_proposals(scope)[1]:
        with contextlib.suppress(OSError):
            f.replace(f.with_name(f.name + ".corrupt"))
    items = sorted(items, key=lambda p: -float(p.get("t") or 0))
    pending = [p for p in items if p["status"] == "pending"]
    rest = [p for p in items if p["status"] != "pending"][: max(0, MAX_KEPT - len(pending))]
    write_atomic(f, {"proposals": sorted(pending + rest, key=lambda p: -float(p.get("t") or 0))})


def load_automations(ws: Path) -> dict:
    try:
        return ta.load(ws)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError, AttributeError) as e:
        raise ToolError(f"{ta.config_file(ws)} cannot be read ({e}); the user must fix it by hand.")


# ---------------------------------------------------------------- validation

def check_name(name) -> str:
    if not isinstance(name, str) or not NAME_RE.fullmatch(name):
        raise ToolError(f"'{name}' is not a routine name: 2-32 lowercase letters, digits and -, starting with a letter "
                        "(e.g. morning-issues).")
    return name


def check_every(every, lim: dict) -> int:
    try:
        secs = ta.seconds(str(every or ""))
    except ValueError:
        raise ToolError(f"'{every}' is not an interval: use a number and m, h or d (e.g. 6h or 1d).") from None
    if secs < lim["min_interval_seconds"]:
        raise ToolError(f"Every {every} is too often: the user allows one run every "
                        f"{lim['min_interval_seconds'] // 60} minutes at most. Propose a longer interval.")
    return secs


def check_budget(budget, lim: dict) -> float:
    if isinstance(budget, bool) or not isinstance(budget, (int, float)) or budget != budget:
        raise ToolError(f"The budget must be a number of US dollars (e.g. {DEFAULT_BUDGET}).")
    if not 0 < budget <= lim["max_budget_usd"]:
        raise ToolError(f"The budget must be above 0 and at most {lim['max_budget_usd']} USD per run (the user's limit).")
    return round(float(budget), 4)


def check_task(task, lim: dict) -> str:
    if not isinstance(task, str) or not task.strip():
        raise ToolError("Say what the routine should do, in plain words.")
    task = task.strip()
    if len(task) > lim["max_task_chars"]:
        raise ToolError(f"The task has {len(task)} characters; the limit is {lim['max_task_chars']}. Say it shorter.")
    if CONTROL_RE.search(task):
        raise ToolError("The task has control characters; write it as plain text.")
    return task


def check_why(why) -> str:
    why = (why or "").strip() if isinstance(why, str) else None
    if why is None or len(why) > WHY_CHARS or CONTROL_RE.search(why) or "\n" in why:
        raise ToolError(f"The reason must be one line of at most {WHY_CHARS} characters.")
    return why


def taken(name: str, cfg: dict, proposals: list[dict], skip_id: str | None = None) -> str | None:
    if name in cfg["routines"]:
        return "a routine"
    if name in cfg["triggers"]:
        return "a trigger"
    if any(p["name"] == name and p["status"] == "pending" and p["id"] != skip_id for p in proposals):
        return "a pending proposal"
    return None


# ---------------------------------------------------------------- what is there

def runs(ws: Path) -> list[dict]:
    """Every run the daemon logged in the workspace, oldest first: {t, kind, name, ok, text}."""
    try:
        lines = (ws / ".tanka" / "automation.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        m = LOG_RE.match(line)
        if not m:
            continue
        try:
            t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        if m.group(5) is None:  # "skipped: over N runs this hour"
            out.append({"t": t, "kind": m.group(2), "name": m.group(3), "ok": False, "text": m.group(4)})
        else:
            out.append({"t": t, "kind": m.group(2), "name": m.group(3), "ok": m.group(5) == "0", "text": m.group(6)})
    return out


def _budget(spec: dict):
    try:
        return float(spec.get("budget", ta.DEFAULT_BUDGET))
    except (TypeError, ValueError):
        return None


def daemon_alive() -> bool:
    try:
        return ta.daemon_pid() is not None
    except OSError:
        return False


def state(scope: str, ws: Path) -> dict:
    check_scope(scope)
    cfg = load_automations(ws)
    last = {}
    for r in runs(ws):
        if r["kind"] == "routine":
            last[r["name"]] = {"t": r["t"], "ok": r["ok"], "text": r["text"]}
    routines = [{"name": n, "every": str(s.get("every", "")), "task": str(s.get("task", "")), "budget": _budget(s),
                 "source": "assistant" if s.get("proposed_by") == "assistant" else "you", "last": last.get(n)}
                for n, s in cfg["routines"].items() if isinstance(s, dict)]
    triggers = [{"name": n, "on": str(s.get("on", "")), "task": str(s.get("task", "")), "budget": _budget(s)}
                for n, s in cfg["triggers"].items() if isinstance(s, dict)]
    proposals, notice = read_proposals(scope)
    keys = ("id", "name", "every", "task", "budget", "why", "t", "status")
    return {"routines": routines, "triggers": triggers,
            "proposals": [{k: p.get(k) for k in keys} for p in proposals[:MAX_KEPT]],
            "limits": limits(), "daemon": daemon_alive(), "notice": notice}


def find(proposals: list[dict], proposal_id: str) -> dict:
    p = next((x for x in proposals if x["id"] == proposal_id), None) if isinstance(proposal_id, str) else None
    if p is None:
        raise ToolError(f"No proposal {proposal_id} in this workspace. Use routines_list to see the ids.")
    return p


# ---------------------------------------------------------------- the assistant's side

def propose(scope: str, ws: Path, name, every, task, budget=DEFAULT_BUDGET, why="") -> dict:
    check_scope(scope)
    lim = limits()
    name, task, why = check_name(name), check_task(task, lim), check_why(why)
    check_every(every, lim)
    budget = check_budget(budget, lim)
    with locked(scope):
        cfg = load_automations(ws)
        proposals, _ = read_proposals(scope)
        what = taken(name, cfg, proposals)
        if what:
            raise ToolError(f"'{name}' is already {what} in this workspace. Use routines_list, and pick another name "
                            "or tell the user it exists.")
        if sum(p["status"] == "pending" for p in proposals) >= lim["max_pending"]:
            raise ToolError(f"There are already {lim['max_pending']} proposals waiting for the user. Tell them; do not "
                            "propose more until they approve or reject some.")
        p = {"id": "p-" + secrets.token_hex(3), "name": name, "every": str(every).strip(), "task": task,
             "budget": budget, "why": why, "t": round(now(), 3), "status": "pending"}
        write_proposals(scope, [p] + proposals)
    return p


def withdraw(scope: str, proposal_id: str) -> dict:
    with locked(scope):
        proposals, _ = read_proposals(scope)
        p = find(proposals, proposal_id)
        if p["status"] != "pending":
            raise ToolError(f"Proposal {proposal_id} is {p['status']}, not pending: only the user can change it now.")
        p["status"], p["closed"] = "withdrawn", round(now(), 3)
        write_proposals(scope, proposals)
    return p


# ---------------------------------------------------------------- the user's side

def write_automations(ws: Path, routines: dict) -> None:
    """Replace only the routines table, keeping every other key of automations.json as it was."""
    f = ta.config_file(ws)
    try:
        raw = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    except (OSError, json.JSONDecodeError) as e:
        raise ToolError(f"{f} cannot be read ({e}); fix it by hand first.")
    raw = raw if isinstance(raw, dict) else {}
    raw["routines"] = routines
    write_atomic(f, raw)


def approve(scope: str, ws: Path, proposal_id: str) -> str:
    check_scope(scope)
    lim = limits()
    with locked(scope):
        proposals, _ = read_proposals(scope)
        p = find(proposals, proposal_id)
        if p["status"] != "pending":
            raise ToolError(f"Proposal {proposal_id} is {p['status']}; only a pending one can be approved.")
        name, task = check_name(p.get("name")), check_task(p.get("task"), lim)
        check_every(p.get("every"), lim)
        budget = check_budget(p.get("budget"), lim)
        cfg = load_automations(ws)
        what = taken(name, cfg, proposals, skip_id=proposal_id)
        if what:
            raise ToolError(f"'{name}' is already {what} in this workspace; reject this proposal or remove that first.")
        total = len(cfg["routines"]) + len(cfg["triggers"])
        if total >= lim["max_automations"]:
            raise ToolError(f"This workspace already has {total} routines and triggers (limit {lim['max_automations']}). "
                            "Remove one first.")
        routines = dict(cfg["routines"])
        routines[name] = {"every": p["every"], "task": task, "budget": f"{budget:g}", "proposed_by": "assistant"}
        write_automations(ws, routines)
        p["status"], p["closed"] = "approved", round(now(), 3)
        write_proposals(scope, proposals)
    return f"+ Routine {name} is active: every {p['every']}, up to {budget:g} USD per run. It runs while the automation daemon is up."


def reject(scope: str, ws: Path, proposal_id: str) -> str:
    with locked(scope):
        proposals, _ = read_proposals(scope)
        p = find(proposals, proposal_id)
        if p["status"] != "pending":
            raise ToolError(f"Proposal {proposal_id} is {p['status']}; only a pending one can be rejected.")
        p["status"], p["closed"] = "rejected", round(now(), 3)
        write_proposals(scope, proposals)
    return f"- Proposal {proposal_id} ({p['name']}) rejected."


def remove(scope: str, ws: Path, name: str) -> str:
    check_scope(scope)
    with locked(scope):
        cfg = load_automations(ws)
        if not isinstance(name, str) or name not in cfg["routines"]:
            raise ToolError(f"There is no routine named {name} in this workspace.")
        routines = {k: v for k, v in cfg["routines"].items() if k != name}
        write_automations(ws, routines)
    return f"- Routine {name} removed."


# ---------------------------------------------------------------- tool output

def cap(text: str, n: int = MAX_OUTPUT) -> str:
    if len(text) <= n:
        return text
    marker = "\n… [output truncated: ask for fewer runs]"
    return text[: n - len(marker)] + marker


def emit(lines: list[str]) -> None:
    print(cap("\n".join(lines)))


def one_line(text, n: int) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def ago(t: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t))


def workspace(scope: str) -> Path:
    """The workspace a tool runs in: Tanka sets TANKA_WORKSPACE; otherwise the named workspace."""
    if os.environ.get("TANKA_WORKSPACE"):
        return Path(os.environ["TANKA_WORKSPACE"]).resolve()
    return (ta.WORKSPACES / check_scope(scope)).resolve()


def list_tool(scope: str, ws: Path) -> None:
    s = state(scope, ws)
    pending = [p for p in s["proposals"] if p["status"] == "pending"]
    lines = [f"{len(s['routines'])} routine(s), {len(s['triggers'])} trigger(s), {len(pending)} pending proposal(s); "
             f"the automation daemon is {'running' if s['daemon'] else 'not running'}."]
    if s["notice"]:
        lines.append(f"! {s['notice']}")
    for r in s["routines"]:
        lines.append(f"routine {r['name']} | every {r['every']} | {r['budget']} USD | made by {r['source']} | "
                     f"{one_line(r['task'], 160)}")
    for t in s["triggers"]:
        lines.append(f"trigger {t['name']} | on {t['on']} | {t['budget']} USD | made by you | {one_line(t['task'], 160)}")
    for p in pending:
        lines.append(f"proposal {p['id']} | {p['name']} | every {p['every']} | {p['budget']} USD | NOT active until the "
                     f"user approves | {one_line(p['task'], 160)}")
    lim = s["limits"]
    lines.append(f"Limits: {lim['max_automations']} automations, one run per {lim['min_interval_seconds'] // 60} min at "
                 f"most, {lim['max_budget_usd']} USD per run, {lim['max_pending']} pending proposals.")
    emit(lines)


def history_tool(scope: str, ws: Path, limit: int = 10) -> None:
    check_scope(scope)
    limit = max(1, min(30, int(limit or 10)))
    done = runs(ws)[-limit:][::-1]
    lines = [f"{len(done)} run(s), newest first:" if done else "No runs logged in this workspace yet."]
    for r in done:
        lines.append(f"{ago(r['t'])} | {r['kind']} {r['name']} | {'ok' if r['ok'] else 'failed'} | {one_line(r['text'], 200)}")
    emit(lines)


def propose_tool(scope: str, ws: Path, name, every, task, budget=DEFAULT_BUDGET, why="") -> None:
    p = propose(scope, ws, name, every, task, budget, why)
    if auto_start():
        try:
            emit([approve(scope, ws, p["id"]), "The user lets proposals start on their own. Each run is one model call capped by that budget."])
        except ToolError as e:
            emit([f"Routine {p['name']} could not start by itself ({e}); it stays a draft ({p['id']}) for the user."])
        return
    emit([f"Proposal {p['id']} saved: routine {p['name']}, every {p['every']}, up to {p['budget']:g} USD per run.",
          "It is a DRAFT and is NOT active. Tell the user it only runs after they approve it, in the Routines tab "
          f"of the page or with: tanka routines approve {scope} {p['id']}",
          "Each run is one model call capped by that budget."])


def withdraw_tool(scope: str, proposal_id: str) -> None:
    p = withdraw(scope, proposal_id)
    emit([f"Proposal {p['id']} ({p['name']}) withdrawn; it will not run."])


def run(main) -> None:
    """Entry point of every tool script: arguments as JSON on stdin, errors as one sentence."""
    try:
        main(json.load(sys.stdin))
    except ToolError as e:
        sys.exit(str(e))
