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
    out.append(f"Running ({len(running)}):")
    out += [line(t, cell_of(t), f"attempt {t.get('attempt') or 1}", f"${t.get('cost_usd') or 0:.2f}") for t in running] or ["(nothing)"]
    lanes = [lane for lane in b["queued"] if not name or lane.get("repo_name") == name]
    out.append(f"Queued ({sum(len(lane['tasks']) for lane in lanes)}):")
    for lane in lanes:
        if lane.get("paused"):
            out.append(f"{lane.get('repo_name') or '?'} is paused: {lane.get('reason') or 'a task did not pass'}")
        out += [line(t, "queued") for t in lane["tasks"]]
    done = [t for t in b["done"] if keep(t)][:DONE_SHOWN]
    out.append("Finished recently:")
    out += [line(t, f"passed at {cell_of(t)}", f"branch {t['branch']}" if t.get("branch") else "") for t in done] or ["(nothing)"]
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
