"""cauce for Tanka: the coding tasks cauce runs, on the page and in the assistant's tools.

cauce (https://github.com/Rixmerz/cauce) routes coding work to one-shot Claude Code
workers and keeps every task, attempt and lane in its own database. This module never
opens that database: it runs the `cauce` program and reads the JSON it prints
(`board --full`, `show --json`, `queue add --json`), so a change inside cauce cannot
break it silently.

**Scope.** A workspace sees and queues only in the repositories the user allowed for it,
in HOME/repos.json, outside every workspace, so no assistant can widen its own reach.
Every call checks that list before it runs cauce.

**What the assistant may do.** Read the board, read one task, queue a task. Queueing
spends nothing: the queue runs when the user starts it on the page or with `cauce work`.

Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

MODULE = Path(__file__).resolve().parent
REPO = MODULE.parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import tanka_kit as kit  # noqa: E402
from tanka_kit import ToolError, clip, read_json, run, write_json  # noqa: E402,F401

# Every setting has a default and an environment variable; see README.md.
HOME = Path(os.environ.get("TANKA_CAUCE_HOME", kit.SHARED / "cauce"))
TIMEOUT = float(os.environ.get("TANKA_CAUCE_TIMEOUT", "20"))
CACHE_SECONDS = 3          # the page polls; one board per workspace every few seconds is plenty
REPO_NAME_RE = re.compile(r"[A-Za-z0-9._-]{1,60}")
TITLE_CHARS, TEXT_CHARS = 120, 4000
DONE_SHOWN = 5             # finished tasks the board tool lists

NEEDS_STATES = ("failed", "blocked", "replan", "needs_approval", "interrupted")
CAUCE_SOURCES = ("cauce", "queue")  # tasks cauce ran; "hook" ones are prompts answered in a session


# ---------------------------------------------------------------- the program

def binary() -> str | None:
    """The `cauce` program: TANKA_CAUCE_BIN, then PATH, then the newest Claude Code plugin install."""
    explicit = os.environ.get("TANKA_CAUCE_BIN")
    if explicit:
        return explicit if Path(explicit).is_file() else None
    found = shutil.which("cauce")
    if found:
        return found
    config = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    installs = sorted(config.glob("plugins/cache/*/cauce/*/bin/cauce"), key=lambda p: p.stat().st_mtime, reverse=True)
    return str(installs[0]) if installs else None


def call(*args: str) -> str:
    exe = binary()
    if exe is None:
        raise ToolError("cauce is not installed on this machine. Tell the user; it installs as a Claude Code plugin "
                        "(claude plugin install cauce@rixmerz), or set TANKA_CAUCE_BIN.")
    try:
        p = subprocess.run([exe, *args], capture_output=True, text=True, timeout=TIMEOUT, check=False)
    except subprocess.TimeoutExpired as e:
        raise ToolError(f"cauce did not answer within {TIMEOUT:.0f} s. Do not retry now; tell the user.") from e
    except OSError as e:
        raise ToolError(f"cauce could not be started ({e}). Tell the user.") from e
    if p.returncode != 0:
        raise ToolError(clip((p.stderr or p.stdout or "").strip() or f"cauce exited {p.returncode}", 300))
    return p.stdout


def call_json(*args: str):
    out = call(*args)
    try:
        return json.loads(out)
    except ValueError as e:
        raise ToolError("cauce printed something that is not JSON; it may be older than this module needs. "
                        "Tell the user to update it.") from e


def version() -> str | None:
    try:
        return call("--version").strip() or None
    except ToolError:
        return None


# ---------------------------------------------------------------- which repositories a workspace may use

def repos_file() -> Path:
    return HOME / "repos.json"


def repos(scope: str) -> dict[str, str]:
    """{name: directory} this workspace may use."""
    data = read_json(repos_file(), {})
    mine = data.get(scope) if isinstance(data, dict) else None
    return {str(k): str(v) for k, v in mine.items()} if isinstance(mine, dict) else {}


def allow(scope: str, directory: str, name: str | None = None) -> str:
    """Let a workspace see and queue in a repository. Run by the user, never by a tool."""
    path = Path(directory).expanduser().resolve()
    if not path.is_dir():
        raise ToolError(f"{path} is not a directory")
    name = name or path.name
    if not REPO_NAME_RE.fullmatch(name):
        raise ToolError(f"'{name}' cannot be a repository name here: use letters, digits, '.', '_' or '-'")
    data = read_json(repos_file(), {})
    data = data if isinstance(data, dict) else {}
    mine = data.setdefault(scope, {})
    if mine.get(name) not in (None, str(path)):
        raise ToolError(f"{scope} already has a repository named {name} ({mine[name]}); pick another name")
    mine[name] = str(path)
    write_json(repos_file(), data)
    return name


def deny(scope: str, name: str) -> bool:
    data = read_json(repos_file(), {})
    if not isinstance(data, dict) or name not in data.get(scope, {}):
        return False
    del data[scope][name]
    if not data[scope]:
        del data[scope]
    write_json(repos_file(), data)
    return True


def directory(scope: str, name: str) -> str:
    mine = repos(scope)
    if name not in mine:
        known = ", ".join(sorted(mine)) or "none"
        raise ToolError(f"'{name}' is not a repository this workspace may use (it may use: {known}). "
                        "Ask the user which one; do not guess.")
    return mine[name]


# ---------------------------------------------------------------- reading

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def board(scope: str, fresh: bool = False) -> dict:
    """cauce's board for this workspace's repositories, each task named by its repository's name here."""
    mine = repos(scope)
    if not mine:
        return {"repos": {}, "keys": {}, "counts": {"needs_you": 0, "running": 0, "queued": 0, "done": 0},
                "needs_you": [], "running": [], "queued": [], "done": []}
    key = json.dumps([scope, sorted(mine.items())])
    with _lock:
        hit = _cache.get(key)
        if hit and not fresh and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
    args = ["board", "--full"]
    for d in mine.values():
        args += ["--repo", d]
    data = call_json(*args)
    by_dir = data.get("repos") or {}
    names = {by_dir.get(d): name for name, d in mine.items() if by_dir.get(d)}

    def named(task: dict) -> dict:
        return dict(task, repo_name=names.get(task.get("repo"), ""))

    out = {
        "repos": mine,
        "keys": names,
        "counts": data.get("counts", {}),
        "needs_you": [named(t) for t in data.get("needs_you", [])],
        "running": [named(t) for t in data.get("running", [])],
        "queued": [dict(lane, repo_name=names.get(lane.get("repo"), ""), tasks=[named(t) for t in lane.get("tasks", [])])
                   for lane in data.get("queued", [])],
        "done": [named(t) for t in data.get("done", [])],
    }
    with _lock:
        _cache[key] = (time.monotonic(), out)
    return out


def task(scope: str, task_id: int) -> dict:
    """One task, refused unless it belongs to a repository this workspace may use."""
    keys = board(scope)["keys"]
    detail = call_json("show", str(int(task_id)), "--json")
    t = detail.get("task") or {}
    if t.get("repo") not in keys:
        raise ToolError(f"task #{task_id} is not in a repository this workspace may use")
    detail["task"] = dict(t, repo_name=keys[t["repo"]])
    return detail


def keys(scope: str) -> dict[str, str]:
    """{cauce repository key: name here} for this workspace's repositories."""
    return board(scope)["keys"] if repos(scope) else {}


def projects(scope: str) -> list[dict]:
    """Every repository cauce has worked in, each marked with its name here when this workspace may use it.
    The user picks among them on the page; the assistant never sees this list."""
    mine, named = repos(scope), keys(scope)
    by_dir = {str(Path(d).resolve()): n for n, d in mine.items()}
    out = []
    for p in call_json("projects", "--json"):
        name = named.get(p.get("repo")) or by_dir.get(str(p.get("dir") or ""))
        out.append(dict(p, name=name, allowed=bool(name)))
    return out


def allow_known(scope: str, where: str, name: str | None = None) -> str:
    """Allow, from the page, a repository cauce has already worked in: only a directory cauce knows."""
    known = {str(p.get("dir")) for p in call_json("projects", "--json") if p.get("exists")}
    if str(where) not in known:
        raise ToolError("cauce has not worked in that directory; allow it from a terminal with tanka cauce allow")
    named = allow(scope, where, name)
    _cache.clear()
    return named


def sessions(scope: str, name: str | None = None) -> list[dict]:
    """The Claude Code sessions cauce saw in this workspace's repositories (or one of them)."""
    mine = repos(scope)
    dirs = [directory(scope, name)] if name else list(mine.values())
    if not dirs:
        return []
    args = ["sessions", "--json"]
    for d in dirs:
        args += ["--repo", d]
    named = keys(scope)
    return [dict(x, repo_name=named.get(x.get("repo"), "")) for x in call_json(*args)]


def problems(scope: str, query: str = "", everywhere: bool = False, limit: int = 40) -> list[dict]:
    """What cauce remembers: problems and every fix tried on them. `everywhere` is cauce's whole memory,
    for the user's page; otherwise only this workspace's repositories, which is all a tool may read."""
    args = ["memory", "list", "--json", "--limit", str(int(limit))]
    if query.strip():
        args += ["--query", " ".join(query.split())[:200]]
    if not everywhere:
        mine = repos(scope)
        if not mine:
            return []
        for d in mine.values():
            args += ["--repo", d]
    named = keys(scope)
    return [dict(x, repo_name=named.get(x.get("repo"), "")) for x in call_json(*args)]


def memory_text(scope: str, query: str = "") -> str:
    if not repos(scope):
        raise ToolError("This workspace may not use any repository yet. Tell the user to allow one with: "
                        "tanka cauce allow <workspace> <directory>")
    found = problems(scope, query)
    if not found:
        return "cauce remembers no problem" + (f" matching '{query}'" if query else "") + " in these repositories."
    out = []
    for x in found[:15]:
        out.append(f"problem #{x['id']} [{x.get('state')}] {clip(x.get('title'), TITLE_CHARS)} ({x.get('repo_name') or '?'})")
        for f in x.get("fixes", []):
            outcome = "disproved" if f.get("invalidated_on") else f.get("outcome")
            why = f" — {clip(f['why'], 160)}" if f.get("why") else ""
            out.append(f"  {outcome}: {clip(f.get('description'), 200)}{why}")
    return "\n".join(out)


# ---------------------------------------------------------------- acting

def queue(scope: str, name: str, text: str, verify: str | None = None) -> dict:
    where = directory(scope, name)
    text = " ".join(str(text or "").split())
    if len(text) < 10:
        raise ToolError("The task is too short to act on. Ask the user what exactly should be done.")
    if len(text) > TEXT_CHARS:
        raise ToolError(f"The task is longer than {TEXT_CHARS} characters; ask the user to split it.")
    args = ["queue", "add", text, "--repo", where, "--json"]
    if verify:
        args += ["--verify", verify]
    out = call_json(*args)
    _cache.clear()
    return dict(out, repo_name=name)


def cancel(scope: str, task_id: int) -> dict:
    t = task(scope, task_id)["task"]
    if t.get("status") not in ("queued", "running"):
        raise ToolError(f"task #{task_id} is {t.get('status')}; there is nothing to cancel")
    call("cancel", str(int(task_id)))
    _cache.clear()
    return {"cancelled": int(task_id)}


def unpause(scope: str, name: str) -> dict:
    call("lanes", "--unpause", directory(scope, name))
    _cache.clear()
    return {"unpaused": name}


def work_log(scope: str, name: str) -> Path:
    return HOME / "work" / f"{scope}--{name}.log"


def work_pid(scope: str, name: str) -> Path:
    return HOME / "work" / f"{scope}--{name}.pid"


def working(scope: str, name: str) -> bool:
    try:
        pid = int(work_pid(scope, name).read_text())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def work(scope: str, name: str) -> dict:
    """Run this repository's queue in cauce workers, detached. Only the user starts it: it spends."""
    where = directory(scope, name)
    if working(scope, name):
        raise ToolError(f"the queue of {name} is already running")
    exe = binary()
    if exe is None:
        call("--version")  # raises the not-installed message
    log = work_log(scope, name)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as fh:
        proc = subprocess.Popen([exe, "work", "--repo", where], cwd=where, stdin=subprocess.DEVNULL,  # noqa: S603
                                stdout=fh, stderr=subprocess.STDOUT, start_new_session=True)
    work_pid(scope, name).write_text(str(proc.pid))
    _cache.clear()
    return {"started": name, "pid": proc.pid}


# ---------------------------------------------------------------- for the assistant

def epoch(iso: str | None) -> float:
    try:
        return datetime.fromisoformat(str(iso)).timestamp()
    except ValueError:
        return 0.0


def cell_of(t: dict) -> str:
    return t.get("current_cell") or t.get("final_cell") or t.get("start_cell") or "-"


def minutes(iso: str | None) -> str:
    seconds = time.time() - epoch(iso) if iso else -1
    if seconds < 0:
        return "?"
    m = int(seconds // 60)
    return "under a minute" if m < 1 else f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


def tries(t: dict) -> str:
    """The way a task took through its ladder: haiku ✗ → sonnet/low ✓."""
    steps = (t.get("flow") or {}).get("steps") or []
    return " → ".join(f"{s['cell']} {'✓' if s.get('passed') else '✗'}" for s in steps)


def line(t: dict, *parts: str) -> str:
    return " | ".join([f"#{t['id']}", t.get("repo_name") or "?", *[p for p in parts if p], clip(t.get("title"), TITLE_CHARS)])


def board_text(scope: str, name: str | None = None) -> str:
    if name:
        directory(scope, name)
    b = board(scope)
    if not b["repos"]:
        raise ToolError("This workspace may not use any repository yet. Tell the user to allow one with: "
                        "tanka cauce allow <workspace> <directory>")
    keep = (lambda t: t.get("repo_name") == name) if name else (lambda t: True)
    out = [f"Repositories: {', '.join(sorted(b['repos']))}"]
    needs = [t for t in b["needs_you"] if keep(t)]
    out.append(f"Needs the user ({len(needs)}):")
    out += [line(t, t.get("status", ""), t.get("asks", "")) for t in needs] or ["(nothing)"]
    running = [t for t in b["running"] if keep(t)]
    agents = [t for t in running if t.get("worker")]
    out.append(f"Agents working ({len(agents)}), each a cauce worker on one attempt:")
    out += [line(t, t["worker"].get("cell") or cell_of(t), f"attempt {t['worker'].get('seq') or 1}",
                 f"running {minutes(t['worker'].get('started_at'))}", f"${t.get('cost_usd') or 0:.2f} so far")
            for t in agents] or ["(none)"]
    between = [t for t in running if t.get("flow") and not t.get("worker")]
    out += [line(t, "between attempts", f"tried {', '.join(s['cell'] for s in t['flow']['steps']) or 'nothing yet'}")
            for t in between]
    lanes = [lane for lane in b["queued"] if not name or lane.get("repo_name") == name]
    out.append(f"Pending ({sum(len(lane['tasks']) for lane in lanes)}):")
    for lane in lanes:
        if lane.get("paused"):
            out.append(f"{lane.get('repo_name') or '?'} is paused: {lane.get('reason') or 'a task did not pass'}")
        out += [line(t, "queued") for t in lane["tasks"]]
    done = [t for t in b["done"] if keep(t) and t.get("source") in CAUCE_SOURCES][:DONE_SHOWN]
    out.append("Done recently:")
    out += [line(t, f"passed at {cell_of(t)}", tries(t), f"branch {t['branch']}" if t.get("branch") else "")
            for t in done] or ["(nothing)"]
    return "\n".join(out)


def task_text(scope: str, task_id: int) -> str:
    d = task(scope, task_id)
    t = d["task"]
    out = [f"#{t['id']} [{t.get('status')}] {clip(t.get('title'), TITLE_CHARS)}",
           f"repository {t['repo_name']} · kind {t.get('kind') or '-'} · cost ${t.get('cost_usd') or 0:.2f}"]
    for a in d.get("attempts", []):
        verdict = "passed" if a.get("passed") else (a.get("failure") or "failed")
        move = f" → {a['move']}: {clip(a.get('move_reason'), 160)}" if a.get("move") else ""
        out.append(f"attempt {a.get('seq')} at {a.get('cell')}: {verdict}{move}")
        if a.get("summary"):
            out.append(f"  {clip(a['summary'], 300)}")
    if d.get("branch"):
        out.append(f"branch to review: {d['branch']}")
    if t.get("status") in NEEDS_STATES:
        out.append("It needs the user: they decide what to do next on the page or in their terminal.")
    return "\n".join(out)
