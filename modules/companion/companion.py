"""Companion for Tanka: a counterpart that watches the user's Claude Code sessions and speaks first.

The module ships the mechanism; each user builds the role (docs/companion.md):

- The **tap** (`tap.py`, hooks in the user's normal Claude Code) appends one
  compact event per hook to `feed/<session>.jsonl`, only for projects listed in
  `watch.json`, with secrets scrubbed before anything is written.
- `events()` turns the feed into **signals** (deterministic, no model) and
  hands the automation daemon only those that wake one of the workspace's
  **lenses** (`.claude/companion/lenses/*.md`, written with `tanka dev`).
- The tools read the feed and the watched repository, and queue **notes**
  for the user. Nothing here writes to a watched project.

What each workspace may see is decided by `watch.json` (in TANKA_COMPANION_HOME,
outside every workspace): a project belongs to exactly one workspace, and
every tool refuses sessions and paths that belong to another.

Standard library only.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

MODULE = Path(__file__).resolve().parent
REPO = MODULE.parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import tanka_common as tc  # noqa: E402

# Every setting has a default and an environment variable; see README.md.
HOME = Path(os.environ.get("TANKA_COMPANION_HOME", Path.home() / ".tanka" / "shared" / "companion"))
WORKSPACES = Path(os.environ.get("TANKA_WORKSPACES", Path.home() / ".tanka" / "workspaces"))
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
FRESH_SECONDS = int(os.environ.get("TANKA_COMPANION_FRESH_SECONDS", "600"))
RECENT_HOURS = int(os.environ.get("TANKA_COMPANION_RECENT_HOURS", "24"))

PROMPT_CHARS, TARGET_CHARS, ERROR_CHARS = 500, 120, 300
DIGEST_CHARS = 5500  # under the 6000 the harness allows, with room for the header
MAX_LENSES = 3
SIGNALS = {
    "turn_end_substantial": "a turn that changed many lines or files",
    "stuck": "the same failure repeating in one session",
    "idle_dirty": "a session gone quiet with uncommitted changes",
}
DEFAULT_THRESHOLDS = {"turn_lines": 30, "turn_files": 3, "stuck": 3, "idle_minutes": 20}
DEFAULT_BUDGET = {"runs_per_hour": 6, "notes_per_hour": 3}
DEFAULT_BRIEF = "09:00"   # the daily brief in the chat; "" turns it off
DEFAULT_STALE_DAYS = 3    # an open item this old is called out in the brief
BRIEF_LATE_HOURS = 12     # a machine that wakes later than this after the brief time skips the day
MAX_BUDGET = {"runs_per_hour": 12, "notes_per_hour": 6}
SPEAKS = ("question", "finding", "reminder")
CARD_KINDS = ("check", "reminder")
CARD_CHARS, TOPIC_CHARS = 200, 40
MAX_AHEAD_DAYS = 60
GENERAL = "general"
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# The harness's own secret patterns, plus the forms that show up in coding sessions.
SECRET_PATTERNS = [re.compile(p) for p in tc.DEFAULT_POLICY["send_validation"]["secret_patterns"] + [
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\bAIza[0-9A-Za-z_-]{35}\b",
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"(?i)\b(api[_-]?key|secret|token|passwd|pwd)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}",
    r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)",
    r"https://hooks\.slack\.com/services/\S+",
    r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}",
]]


class ToolError(Exception):
    """A failure the model should relay: the message says what to do next."""


def run(main) -> None:
    """Entry point of every tool script: arguments as JSON on stdin, errors as one sentence."""
    try:
        main(json.load(sys.stdin))
    except ToolError as e:
        sys.exit(str(e))


def scrub(text) -> str:
    s = str(text or "")
    for pat in SECRET_PATTERNS:
        s = pat.sub("[secret]", s)
    return s


def clip(text, n: int) -> str:
    s = " ".join(scrub(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def tail_clip(text, n: int) -> str:
    """Like clip, but keeps the end: an error's last lines say what went wrong."""
    s = " ".join(scrub(text).split())
    return s if len(s) <= n else "…" + s[-(n - 1):]


def now() -> float:
    return time.time()


def hhmm(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M")


def day_time(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise ToolError(f"{path} is not valid JSON (line {e.lineno}). Tell the user; it must be fixed by hand.")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


# ---------------------------------------------------------------- scope: watch.json

def watch_file() -> Path:
    return HOME / "watch.json"


def real(path) -> str:
    return str(Path(os.path.expanduser(str(path))).resolve())


def watch() -> dict[str, str]:
    """{real project path: workspace name}. Only the user edits it (`tanka companion watch`)."""
    data = read_json(watch_file(), {})
    return {real(p): ws for p, ws in (data.get("projects") or {}).items()}


def owner(cwd) -> tuple[str, str] | None:
    """(project, workspace) for a directory inside a watched project; the deepest match wins."""
    if not cwd:
        return None
    here = real(cwd)
    best = None
    for project, ws in watch().items():
        if (here == project or here.startswith(project + os.sep)) and (best is None or len(project) > len(best[0])):
            best = (project, ws)
    return best


def projects_of(scope: str) -> list[str]:
    return sorted(p for p, ws in watch().items() if ws == scope)


def watch_set(path: str, scope: str | None) -> str:
    data = read_json(watch_file(), {})
    projects = {real(p): ws for p, ws in (data.get("projects") or {}).items()}
    key = real(path)
    if scope is None:
        if projects.pop(key, None) is None:
            raise ToolError(f"{key} is not watched.")
    else:
        if not Path(key).is_dir():
            raise ToolError(f"{key} is not a directory.")
        projects[key] = scope
    write_json(watch_file(), {"projects": projects})
    return key


# ---------------------------------------------------------------- the tap

def feed_dir() -> Path:
    return HOME / "feed"


def safe_id(s) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", str(s or ""))[:64] or "unknown"


def git_out(project: str, *args: str, timeout: int = 3) -> str:
    """Plain read of git metadata for the tap; never raises."""
    try:
        p = subprocess.run(["git", "-C", project, "-c", "core.fsmonitor=false", *args], capture_output=True,
                           text=True, timeout=timeout, env=git_env(), stdin=subprocess.DEVNULL)
        return p.stdout.strip() if p.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def tool_event(inp: dict, ok: bool) -> dict:
    name = str(inp.get("tool_name") or "?")
    ti = inp.get("tool_input") if isinstance(inp.get("tool_input"), dict) else {}
    target = next((ti[k] for k in ("file_path", "notebook_path", "command", "path", "pattern", "url", "description") if ti.get(k)), "")
    ev = {"e": "tool", "tool": name, "target": clip(target, TARGET_CHARS), "ok": ok}
    if name in EDIT_TOOLS:
        edits = ti.get("edits") if isinstance(ti.get("edits"), list) else [ti]
        lines = 0
        for ed in edits:
            if isinstance(ed, dict):
                new = str(ed.get("new_string") or ed.get("content") or ed.get("new_source") or "")
                old = str(ed.get("old_string") or "")
                lines += max(new.count("\n") + 1 if new else 0, old.count("\n") + 1 if old else 0)
        ev["lines"] = lines
        ev["file"] = clip(ti.get("file_path") or ti.get("notebook_path") or "", TARGET_CHARS)
    if not ok:
        raw = scrub(inp.get("error") or inp.get("tool_response") or "")
        ev["error"] = tail_clip(raw, ERROR_CHARS)
        ev["sig"] = error_signature(raw)  # before the lines are flattened
    return ev


def tap_event(inp: dict) -> dict | None:
    """The feed line for one hook call, or None when the session is not in a watched project."""
    o = owner(inp.get("cwd"))
    if not o:
        return None
    project, _ = o
    hook = inp.get("hook_event_name")
    if hook == "SessionStart":
        cwd = real(inp.get("cwd"))
        ev = {"e": "session_start", "source": str(inp.get("source") or ""), "cwd": cwd,
              "branch": git_out(cwd, "rev-parse", "--abbrev-ref", "HEAD")}
    elif hook == "UserPromptSubmit":
        ev = {"e": "prompt", "text": clip(inp.get("prompt"), PROMPT_CHARS)}
    elif hook == "PostToolUse":
        resp = inp.get("tool_response")
        ev = tool_event(inp, not (isinstance(resp, dict) and resp.get("is_error")))
    elif hook == "PostToolUseFailure":
        ev = tool_event(inp, False)
    elif hook == "Stop":
        ev = {"e": "turn_end"}
    elif hook == "SessionEnd":
        ev = {"e": "session_end", "reason": str(inp.get("reason") or "")}
    else:
        return None
    return {"t": round(now(), 3), "session": safe_id(inp.get("session_id")), "project": project, **ev}


def tap(inp: dict) -> None:
    ev = tap_event(inp)
    if ev is None:
        return
    feed_dir().mkdir(parents=True, exist_ok=True)
    with (feed_dir() / f"{ev['session']}.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- reading the feed

def read_feed(path: Path) -> list[dict]:
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except FileNotFoundError:
        pass
    return out


def sessions_of(scope: str, hours: int | None = RECENT_HOURS) -> dict[str, list[dict]]:
    """{session id: events} for the sessions of this workspace's projects, newest first."""
    mine = set(projects_of(scope))
    found = []
    for f in feed_dir().glob("*.jsonl") if feed_dir().is_dir() else []:
        if hours is not None and now() - f.stat().st_mtime > hours * 3600:
            continue
        evs = read_feed(f)
        if evs and evs[0].get("project") in mine:
            found.append((evs[-1].get("t", 0), f.stem, evs))
    return {sid: evs for _, sid, evs in sorted(found, reverse=True)}


def session_of(scope: str, session: str | None) -> tuple[str, list[dict]]:
    sessions = sessions_of(scope, hours=None if session else RECENT_HOURS)
    if not sessions:
        raise ToolError("No watched session yet. The user adds projects with `tanka companion watch add <path> <workspace>`; tell them.")
    if not session:
        sid = next(iter(sessions))
        return sid, sessions[sid]
    hits = [s for s in sessions if s.startswith(safe_id(session))]
    if len(hits) != 1:
        raise ToolError(f"No single watched session starts with '{session}'. Use companion_sessions to see the ids.")
    return hits[0], sessions[hits[0]]


# ---------------------------------------------------------------- signals (no model)

def error_signature(error: str) -> str:
    """What makes two failures 'the same': the last meaningful line, numbers and hex blurred."""
    lines = [ln.strip() for ln in re.split(r"\n|(?<=\.)\s{2,}", str(error)) if ln.strip()]
    lines = [ln for ln in lines if not re.fullmatch(r"(?i)exit code \d+", ln)] or lines
    last = re.sub(r"(?i)^exit code \d+\s*", "", lines[-1] if lines else "")
    return re.sub(r"0x[0-9a-f]+|\d+", "#", last)[:120]


def firings(events: list[dict], th: dict) -> list[dict]:
    """Every catalog signal the events cross, in order: {signal, key, i, t, evidence}. Pure and replayable."""
    out, fails = [], {}
    turn_lines, turn_files = 0, set()
    for i, ev in enumerate(events):
        kind = ev.get("e")
        if kind == "prompt":
            turn_lines, turn_files = 0, set()
        elif kind == "tool":
            if ev.get("tool") in EDIT_TOOLS and ev.get("ok"):
                turn_lines += int(ev.get("lines") or 0)
                if ev.get("file"):
                    turn_files.add(ev["file"])
            if not ev.get("ok"):
                sig = (ev.get("tool"), ev.get("sig") or error_signature(ev.get("error", "")))
                fails[sig] = fails.get(sig, 0) + 1
                if fails[sig] == th["stuck"]:
                    out.append({"signal": "stuck", "key": "stuck:" + hashlib.sha256(repr(sig).encode()).hexdigest()[:10],
                                "i": i, "t": ev.get("t", 0),
                                "evidence": f"{sig[0]} failed {th['stuck']} times with: {sig[1] or '(no message)'}; last target: {ev.get('target', '')}"})
        elif kind == "turn_end":
            if turn_lines >= th["turn_lines"] or len(turn_files) >= th["turn_files"]:
                files = ", ".join(sorted(turn_files)[:5])
                out.append({"signal": "turn_end_substantial", "key": f"turn:{i}", "i": i, "t": ev.get("t", 0),
                            "evidence": f"the turn changed ~{turn_lines} lines in {len(turn_files)} file(s): {files}"})
            turn_lines, turn_files = 0, set()
    return out


def idle_firings(events: list[dict], th: dict, until: float | None = None) -> list[dict]:
    """A quiet stretch of idle_minutes after activity: between events, and (live) up to `until`."""
    gap, out = th["idle_minutes"] * 60, []
    for i, ev in enumerate(events):
        nxt = events[i + 1]["t"] if i + 1 < len(events) else until
        if nxt is not None and nxt - ev.get("t", 0) >= gap and ev.get("e") != "session_end":
            out.append({"signal": "idle_dirty", "key": f"idle:{i}", "i": i, "t": ev.get("t", 0) + gap,
                        "evidence": f"no activity for {int((nxt - ev.get('t', 0)) // 60)} min after {hhmm(ev.get('t', 0))}"})
    return out


# ---------------------------------------------------------------- the user's companion: config and lenses

def ws_dir(scope: str) -> Path:
    return (WORKSPACES / scope).resolve()


def config_file(ws: Path) -> Path:
    return ws / ".claude" / "companion.json"


def lenses_dir(ws: Path) -> Path:
    return ws / ".claude" / "companion" / "lenses"


def load_config(ws: Path) -> dict:
    raw = read_json(config_file(ws), {})
    return {"lenses": list(raw.get("lenses") or []), "proactivity": int(raw.get("proactivity", 1)),
            "notify": bool(raw.get("notify", False)), "quiet_hours": str(raw.get("quiet_hours") or ""),
            "budget": {**DEFAULT_BUDGET, **(raw.get("budget") or {})},
            "thresholds": {**DEFAULT_THRESHOLDS, **(raw.get("thresholds") or {})},
            "brief": str(raw.get("brief", DEFAULT_BRIEF) or ""), "stale_days": raw.get("stale_days", DEFAULT_STALE_DAYS)}


def parse_lens(text: str) -> dict:
    meta, body = {}, text
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if m:
        body = m.group(2)
        for line in m.group(1).splitlines():
            k, sep, v = line.partition(":")
            if sep:
                v = v.split("#", 1)[0].strip()
                meta[k.strip()] = [x.strip() for x in v.strip("[]").split(",") if x.strip()] if v.startswith("[") else v
    sections, current = {}, None
    for line in body.splitlines():
        h = re.match(r"^##\s+(.+?)\s*$", line)
        if h:
            current = h.group(1).strip().lower()
            sections[current] = []
        elif current:
            sections[current].append(line)
    return {"meta": meta, "sections": {k: "\n".join(v).strip() for k, v in sections.items()}, "text": text.strip()}


def bullets(section: str) -> list[str]:
    return [ln.strip()[2:].strip() for ln in section.splitlines() if ln.strip().startswith(("- ", "* "))]


def load_lenses(ws: Path) -> dict[str, dict]:
    out = {}
    for f in sorted(lenses_dir(ws).glob("*.md")) if lenses_dir(ws).is_dir() else []:
        out[f.stem] = dict(parse_lens(f.read_text(encoding="utf-8")), path=f)
    return out


def lens_problems(name: str, lens: dict) -> list[str]:
    meta, sec, p = lens["meta"], lens["sections"], []
    if meta.get("name") != name:
        p.append(f"lens {name}: frontmatter name must be '{name}' (the file name)")
    wakes = meta.get("wakes_on") if isinstance(meta.get("wakes_on"), list) else []
    if not wakes:
        p.append(f"lens {name}: wakes_on must name at least one signal ({', '.join(SIGNALS)}), so it never runs on every event")
    for s in wakes:
        if s not in SIGNALS:
            p.append(f"lens {name}: unknown signal '{s}' (known: {', '.join(SIGNALS)})")
    if meta.get("speaks") not in SPEAKS:
        p.append(f"lens {name}: speaks must be one of {', '.join(SPEAKS)}")
    if meta.get("severity", "low") != "low":
        p.append(f"lens {name}: severity may only be 'low' for now (nudges come with the guardian signals)")
    rubric = sec.get("rubric", "")
    if not re.search(r"^\s*1[.)]\s+\S", rubric, re.M):
        p.append(f"lens {name}: '## Rubric' needs numbered criteria (1. …) that can say no")
    good, bad = bullets(sec.get("say it like this", "")), bullets(sec.get("never like this", ""))
    if len(good) < 1:
        p.append(f"lens {name}: '## Say it like this' needs at least one example")
    if len(bad) < 2:
        p.append(f"lens {name}: '## Never like this' needs at least two examples")
    if meta.get("speaks") == "question" and any("?" not in g for g in good):
        p.append(f"lens {name}: speaks question, so every 'Say it like this' example must be a question")
    return p


def check(ws: Path) -> tuple[list[str], list[str]]:
    """(problems, warnings) for a workspace's companion; no problems means it may run."""
    problems, warnings = [], []
    if not config_file(ws).is_file():
        return [f"{config_file(ws)} does not exist: run `tanka install companion <workspace>` first"], []
    try:
        cfg = load_config(ws)
    except (ToolError, ValueError, TypeError) as e:
        return [str(e)], []
    lenses = load_lenses(ws)
    active = cfg["lenses"]
    if not active:
        problems.append("no active lens: build one with /new-companion in `tanka dev <workspace>`")
    if len(active) > MAX_LENSES:
        problems.append(f"{len(active)} active lenses: at most {MAX_LENSES}, every extra one competes for the same attention")
    for name in active:
        if name not in lenses:
            problems.append(f"lens {name} is active but {lenses_dir(ws) / (name + '.md')} does not exist")
        else:
            problems += lens_problems(name, lenses[name])
    for k, cap in MAX_BUDGET.items():
        v = cfg["budget"].get(k)
        if not isinstance(v, int) or not 1 <= v <= cap:
            problems.append(f"budget.{k} must be an integer from 1 to {cap}")
    if cfg["proactivity"] not in (0, 1):
        problems.append("proactivity must be 0 (off) or 1 (whisper)")
    if cfg["quiet_hours"] and not re.fullmatch(r"\d\d:\d\d-\d\d:\d\d", cfg["quiet_hours"]):
        problems.append("quiet_hours must look like 20:00-08:00")
    if cfg["brief"] and brief_time(cfg["brief"]) is None:
        problems.append("brief must be a time like 09:00, or empty to turn it off")
    if not isinstance(cfg["stale_days"], int) or not 1 <= cfg["stale_days"] <= 60:
        problems.append("stale_days must be an integer from 1 to 60")
    for k in DEFAULT_THRESHOLDS:
        if not isinstance(cfg["thresholds"].get(k), int) or cfg["thresholds"][k] < 1:
            problems.append(f"thresholds.{k} must be a positive integer")
    if not projects_of(ws.name):
        warnings.append(f"no project is watched for '{ws.name}': tanka companion watch add <path> {ws.name}")
    return problems, warnings


def awake(ws: Path, signal: str) -> list[str]:
    cfg, lenses = load_config(ws), load_lenses(ws)
    return [n for n in cfg["lenses"] if n in lenses and signal in (lenses[n]["meta"].get("wakes_on") or [])]


def quiet(spec: str, t: float) -> bool:
    if not spec:
        return False
    a, b = spec.split("-")
    cur = datetime.fromtimestamp(t).strftime("%H:%M")
    return (a <= cur < b) if a <= b else (cur >= a or cur < b)


# ---------------------------------------------------------------- events for the automation daemon

def state_file() -> Path:
    return HOME / "state.json"


def event_for(scope: str, sid: str, project: str, f: dict, lenses: list[str]) -> dict:
    return {"source": "companion", "scope": scope, "match": f["signal"], "signal": f["signal"], "session": sid,
            "project": project, "at": f["t"],
            "what": (f"the signal {f['signal']} in session {sid[:8]} of {project}: {f['evidence']}. "
                     f"Lenses awake: {', '.join(lenses)}. Start with companion_digest session={sid[:8]} signal={f['signal']}.")}


def repo_of(evs: list[dict]) -> str:
    """Where git runs for a session: the directory it started in, when that is inside its watched project."""
    project = evs[0].get("project", "")
    cwd = next((e.get("cwd") for e in evs if e.get("e") == "session_start" and e.get("cwd")), "")
    return cwd if cwd == project or cwd.startswith(project + os.sep) else project


def dirty(project: str) -> bool:
    return bool(git_out(project, "status", "--porcelain", "--ignore-submodules=all", timeout=5))


def events() -> list[dict]:
    """New signals that wake a lens, one dict per line for `tanka companion events`. No prompt text."""
    fire_reminders()
    daily_brief()
    st = read_json(state_file(), {})
    emitted, runs, out, t = st.setdefault("emitted", {}), st.setdefault("runs", {}), [], now()
    for scope in sorted(set(watch().values())):
        ws = ws_dir(scope)
        try:
            problems, _ = check(ws)
        except ToolError:
            continue
        if problems:
            continue  # a companion that does not pass check never runs
        cfg = load_config(ws)
        if cfg["proactivity"] == 0:
            continue
        recent = [x for x in runs.get(scope, []) if t - x < 3600]
        for sid, evs in sessions_of(scope).items():
            done = set(emitted.get(sid, []))
            project = evs[0].get("project", "")
            for f in firings(evs, cfg["thresholds"]) + idle_firings(evs, cfg["thresholds"], until=t):
                if f["key"] in done:
                    continue
                done.add(f["key"])  # decided once: fired, too old, quiet hours, clean tree or over budget
                lenses = awake(ws, f["signal"])
                if not lenses or t - f["t"] > FRESH_SECONDS or quiet(cfg["quiet_hours"], t):
                    continue
                if f["signal"] == "idle_dirty" and not dirty(repo_of(evs)):
                    continue
                if len(recent) >= cfg["budget"]["runs_per_hour"]:
                    continue
                recent.append(t)
                out.append(event_for(scope, sid, project, f, lenses))
            emitted[sid] = sorted(done)
        runs[scope] = recent
    write_json(state_file(), st)
    return out


# ---------------------------------------------------------------- tools

def status_of(evs: list[dict]) -> str:
    if evs[-1].get("e") == "session_end":
        return "ended"
    return "active" if now() - evs[-1].get("t", 0) < 15 * 60 else "idle"


def list_sessions(scope: str) -> None:
    sessions = sessions_of(scope)
    mine = projects_of(scope)
    if not mine:
        raise ToolError("This companion watches no project yet. Tell the user to run `tanka companion watch add <path> <workspace>`.")
    print(f"{len(sessions)} session(s) in the last {RECENT_HOURS} h, watching {len(mine)} project(s): {', '.join(mine)}")
    for sid, evs in sessions.items():
        start = next((e for e in evs if e.get("e") == "session_start"), evs[0])
        prompts = sum(e.get("e") == "prompt" for e in evs)
        tools = [e for e in evs if e.get("e") == "tool"]
        fails = sum(not e.get("ok") for e in tools)
        print(f"{sid[:8]} | {evs[0].get('project')} | {start.get('branch') or '?'} | {hhmm(evs[0]['t'])}-{hhmm(evs[-1]['t'])} | "
              f"{prompts} prompts | {len(tools)} tools, {fails} failed | {status_of(evs)}")
    notes = read_notes(scope)[-5:]
    if notes:
        print(f"Your last {len(notes)} note(s):")
        for n in notes:
            print(f"  {n['id']} {day_time(n['t'])} [{n['lens']}] {n['text']}{'' if n.get('seen') else '  (not seen yet)'}")


def digest_line(ev: dict) -> str:
    t, kind = hhmm(ev.get("t", 0)), ev.get("e")
    if kind == "prompt":
        return f"{t} user: {ev.get('text', '')}"
    if kind == "tool":
        extra = f" (+{ev['lines']} lines)" if ev.get("lines") else ""
        fail = f" FAILED: {ev.get('error', '')}" if not ev.get("ok") else ""
        return f"{t} {ev.get('tool')} {ev.get('target', '')}{extra}{fail}"
    if kind == "turn_end":
        return f"{t} -- turn ended --"
    if kind == "session_start":
        return f"{t} session {ev.get('source') or 'started'} on branch {ev.get('branch') or '?'}"
    if kind == "session_end":
        return f"{t} session ended ({ev.get('reason') or '?'})"
    return f"{t} {kind}"


def digest(scope: str, session: str | None, signal: str | None, limit: int) -> None:
    sid, evs = session_of(scope, session)
    ws = ws_dir(scope)
    head = [f"Session {sid[:8]} in {evs[0].get('project')}, {len(evs)} events, {status_of(evs)}; the last {min(limit, len(evs))}:"]
    if signal:
        if signal not in SIGNALS:
            raise ToolError(f"Unknown signal '{signal}'. Known: {', '.join(SIGNALS)}.")
        lenses = load_lenses(ws)
        woken = awake(ws, signal)
        head.insert(0, f"Lenses awake for {signal}: {', '.join(woken) or 'none (say nothing)'}")
        for n in woken:
            head.insert(1, f"=== lens {n} ===\n{lenses[n]['text']}\n=== end of lens {n} ===")
    lines, size = [], 0
    for ev in reversed(evs[-limit:]):
        ln = digest_line(ev)
        if size + len(ln) > DIGEST_CHARS - sum(len(h) for h in head):
            lines.append("(older events cut: ask for a smaller limit)")
            break
        lines.append(ln)
        size += len(ln) + 1
    print("\n".join(head + list(reversed(lines))))


def git_env() -> dict:
    return {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0",
            "GIT_PAGER": "cat", "PAGER": "cat"}


def safe_git(project: str, args: list[str], timeout: int = 20) -> str:
    """git that reads and never runs the repository's own programs (fsmonitor, filters, external diff, textconv)."""
    base = ["git", "--no-pager", "-C", project, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null",
            "-c", "core.attributesFile=/dev/null", "-c", "diff.external=", "-c", "core.pager=cat"]
    listed = subprocess.run(base + ["config", "--get-regexp", r"^filter\."], capture_output=True, text=True,
                            timeout=timeout, env=git_env(), stdin=subprocess.DEVNULL).stdout
    for name in sorted({ln.split()[0].split(".")[1] for ln in listed.splitlines() if ln.count(".") >= 2}):
        base += ["-c", f"filter.{name}.clean=cat", "-c", f"filter.{name}.smudge=cat",
                 "-c", f"filter.{name}.process=", "-c", f"filter.{name}.required=false"]
    for attempt in (base[:1] + [f"--attr-source={EMPTY_TREE}"] + base[1:], base):
        p = subprocess.run(attempt + args, capture_output=True, text=True, timeout=timeout, env=git_env(),
                           stdin=subprocess.DEVNULL)
        if p.returncode == 0:
            return p.stdout
        if "attr-source" not in p.stderr:
            break
    raise ToolError(f"git failed in {project}: {clip(p.stderr, 200)}. Tell the user; do not retry.")


VIEWS = {
    "status": ["status", "--short", "--branch", "--ignore-submodules=all"],
    "stat": ["diff", "HEAD", "--stat", "--no-ext-diff", "--no-textconv", "--ignore-submodules=all"],
    "diff": ["diff", "HEAD", "--no-ext-diff", "--no-textconv", "--no-color", "--ignore-submodules=all"],
    "log": ["log", "--oneline", "--no-color", "-15"],
}


def diff(scope: str, session: str | None, view: str, max_lines: int) -> None:
    if os.environ.get("TANKA_COMPANION_BACKTEST"):
        raise ToolError("This is a backtest: the repository today is not what it was then, so there is no diff. Judge from the digest.")
    sid, evs = session_of(scope, session)
    project = evs[0].get("project", "")
    if project not in projects_of(scope):
        raise ToolError("That session's project is not watched by this companion. Tell the user.")
    repo = repo_of(evs)
    out = scrub(safe_git(repo, VIEWS[view])).splitlines()
    print(f"git {view} of {repo} (session {sid[:8]}), {len(out)} line(s){', first ' + str(max_lines) if len(out) > max_lines else ''}:")
    print("\n".join(out[:max_lines]) if out else "(nothing)")


# ---------------------------------------------------------------- notes: how the companion speaks

def notes_file(scope: str) -> Path:
    return HOME / "notes" / f"{safe_id(scope)}.jsonl"


def read_notes(scope: str) -> list[dict]:
    return read_feed(notes_file(scope))


def claim_hash(lens: str, text: str, evidence: str) -> str:
    norm = lambda s: re.sub(r"[\W\d_]+", " ", s.lower()).strip()  # noqa: E731
    return hashlib.sha256(f"{lens}|{norm(text)}|{norm(evidence)[:80]}".encode()).hexdigest()[:12]


def note(scope: str, lens: str, text: str, evidence: str, session: str | None) -> None:
    ws = ws_dir(scope)
    cfg, lenses = load_config(ws), load_lenses(ws)
    if lens not in cfg["lenses"] or lens not in lenses:
        raise ToolError(f"'{lens}' is not an active lens of this companion (active: {', '.join(cfg['lenses']) or 'none'}). Say nothing.")
    if len(evidence.strip()) < 8:
        raise ToolError("A note needs its evidence: the event, file or command it is about. Without it, say nothing.")
    if lenses[lens]["meta"].get("speaks") == "question" and "?" not in text:
        raise ToolError(f"The lens {lens} only asks questions. Rewrite the note as a question, or say nothing.")
    sid = session_of(scope, session)[0] if session else ""
    t, notes = now(), read_notes(scope)
    h = claim_hash(lens, text, evidence)
    if any(n.get("hash") == h for n in notes):
        raise ToolError("This was already said to the user. Do not say it again; say nothing.")
    if sum(t - n.get("t", 0) < 3600 for n in notes) >= cfg["budget"]["notes_per_hour"]:
        raise ToolError("The note budget for this hour is spent. Say nothing now.")
    n = {"id": hashlib.sha256(f"{t}{text}".encode()).hexdigest()[:6], "t": round(t, 3), "lens": lens,
         "text": clip(text, 400), "evidence": clip(evidence, 300), "session": sid, "hash": h, "seen": False}
    notes_file(scope).parent.mkdir(parents=True, exist_ok=True)
    with notes_file(scope).open("a", encoding="utf-8") as f:
        f.write(json.dumps(n, ensure_ascii=False) + "\n")
    if cfg["notify"] and not os.environ.get("TANKA_COMPANION_BACKTEST"):
        tc.desktop_notify(f"Companion · {scope} · {lens}", n["text"])
    print(f"Noted {n['id']} for the user ({lens}). It shows in their status line and in `tanka companion notes`. "
          "Do not repeat it; end the run.")


def mark_seen(scope: str) -> list[dict]:
    notes = read_notes(scope)
    fresh = [n for n in notes if not n.get("seen")]
    if fresh:
        for n in notes:
            n["seen"] = True
        notes_file(scope).write_text("".join(json.dumps(n, ensure_ascii=False) + "\n" for n in notes), encoding="utf-8")
    return fresh


def rate(note_id: str, verdict: str) -> dict:
    """Record the user's verdict on a note (good or bad) and mark it seen; returns the note."""
    if verdict not in ("good", "bad"):
        raise ToolError("The verdict is good or bad.")
    for f in (HOME / "notes").glob("*.jsonl") if (HOME / "notes").is_dir() else []:
        notes = read_feed(f)
        for n in notes:
            if n.get("id") == note_id:
                with (HOME / "feedback.jsonl").open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({"t": round(now(), 3), "id": note_id, "scope": f.stem, "lens": n["lens"],
                                         "verdict": verdict}) + "\n")
                n["seen"] = True
                f.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in notes), encoding="utf-8")
                return dict(n, scope=f.stem)
    raise ToolError(f"No note {note_id}. See them with: tanka companion notes --all")


def verdicts() -> dict[str, str]:
    """{note id: the user's latest verdict}."""
    return {r["id"]: r["verdict"] for r in read_feed(HOME / "feedback.jsonl") if r.get("id")}


def unseen() -> int:
    d = HOME / "notes"
    return sum(not n.get("seen") for f in (d.glob("*.jsonl") if d.is_dir() else []) for n in read_feed(f))


# ---------------------------------------------------------------- cards: checks and reminders

# A workspace's cards live in cards/<workspace>.json. A **check** card holds the pending items of
# one topic (a watched project, or any subject the user names); a **reminder** is one thing at one
# time. Both are written by the user (the page) and by the assistant (companion_card), never deleted
# by the assistant: it can add and tick, the user archives.

def cards_file(scope: str) -> Path:
    return HOME / "cards" / f"{safe_id(scope)}.json"


@contextlib.contextmanager
def card_store(scope: str, write: bool = False):
    """The scope's cards under an exclusive lock: the page, the tools and the daemon all write here."""
    f = cards_file(scope)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f.with_suffix(".lock"), "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_json(f, {"cards": []})
        yield data["cards"]
        if write:
            write_json(f, data)


def card_scopes() -> list[str]:
    """Every workspace with a companion, watched or not: reminders work without lenses."""
    found = set(watch().values())
    if WORKSPACES.is_dir():
        found |= {d.name for d in WORKSPACES.iterdir() if (d / ".claude" / "companion.json").is_file()}
    return sorted(found)


def norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def topic_of(scope: str, topic: str | None) -> tuple[str, str]:
    """(label, project path or ''): a watched project's folder name or path becomes that project."""
    raw = re.sub(r"\s+", " ", str(topic or "")).strip()
    if not raw:
        return GENERAL, ""
    for p in projects_of(scope):
        if raw.lower() in (Path(p).name.lower(), p.lower(), real(raw).lower()):
            return Path(p).name, p
    if len(raw) > TOPIC_CHARS:
        raise ToolError(f"A topic is a short name (at most {TOPIC_CHARS} characters), like a project or a subject.")
    return raw, ""


def parse_at(at: str, t: float) -> float:
    """'HH:MM' (today, or tomorrow if already past) or 'YYYY-MM-DD HH:MM', local time."""
    at = str(at or "").strip()
    base = datetime.fromtimestamp(t)
    try:
        if re.fullmatch(r"\d{1,2}:\d{2}", at):
            h, m = map(int, at.split(":"))
            when = base.replace(hour=h, minute=m, second=0, microsecond=0)
            if when.timestamp() < t - 60:
                when += timedelta(days=1)
        else:
            when = datetime.strptime(at, "%Y-%m-%d %H:%M")
    except ValueError:
        raise ToolError(f"'{at}' is not a time. Use HH:MM for today (e.g. 17:30) or YYYY-MM-DD HH:MM.") from None
    if when.timestamp() < t - 60:
        raise ToolError(f"{when:%Y-%m-%d %H:%M} is already past (now {base:%Y-%m-%d %H:%M}). Ask the user when.")
    if when.timestamp() > t + MAX_AHEAD_DAYS * 86400:
        raise ToolError(f"A reminder is at most {MAX_AHEAD_DAYS} days ahead. Ask the user for a nearer date.")
    return when.timestamp()


def by_companion_last_hour(cards: list[dict], t: float) -> int:
    """Cards a lens added on its own (they carry evidence) in the last hour."""
    things = [c for c in cards if c["kind"] == "reminder"] + [i for c in cards if c["kind"] == "check" for i in c["items"]]
    return sum(x.get("by") == "companion" and x.get("evidence") and t - x.get("t", 0) < 3600 for x in things)


def add_card(scope: str, kind: str, topic: str | None, text: str, at: str | None = None,
             by: str = "user", evidence: str = "") -> dict:
    """Add a check item (to its topic's card, made if missing) or a reminder. Adding the same open
    item or reminder twice returns the existing one."""
    if kind not in CARD_KINDS:
        raise ToolError(f"kind is {' or '.join(CARD_KINDS)}.")
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        raise ToolError("Say what the card is about: text cannot be empty.")
    if len(text) > CARD_CHARS:
        raise ToolError(f"Keep it under {CARD_CHARS} characters: one pending thing per card or item.")
    label, project = topic_of(scope, topic)
    if kind == "check" and at:
        raise ToolError("A check item has no time; for something at a time use kind reminder.")
    if kind == "reminder" and not at:
        raise ToolError("A reminder needs at: HH:MM for today or YYYY-MM-DD HH:MM. If the user did not say when, ask.")
    t = now()
    when = parse_at(at, t) if kind == "reminder" else None
    meta = {"t": round(t, 3), "by": by}
    if evidence:
        meta["evidence"] = clip(scrub(evidence), 300)
    with card_store(scope, write=True) as cards:
        if by == "companion" and evidence:  # found by a lens on its own; what the user asks for is not rationed
            budget = load_config(ws_dir(scope))["budget"]["notes_per_hour"] if config_file(ws_dir(scope)).is_file() else 3
            if by_companion_last_hour(cards, t) >= budget:
                raise ToolError("The budget for this hour is spent. Do not add it now; say nothing.")
        live = [c for c in cards if not c.get("archived") and c["topic"].lower() == label.lower()]
        if kind == "reminder":
            same = [c for c in live if c["kind"] == "reminder" and not c.get("done_at") and norm_text(c["text"]) == norm_text(text)]
            if same:
                return dict(same[0], existed=True)
            card = {"id": "r-" + secrets.token_hex(3), "kind": "reminder", "topic": label, "project": project,
                    "text": scrub(text), "at": when, "fired_at": None, "done_at": None, **meta}
            cards.append(card)
            return card
        card = next((c for c in live if c["kind"] == "check"), None)
        if card is None:
            card = {"id": "c-" + secrets.token_hex(3), "kind": "check", "topic": label, "project": project, "items": [], **meta}
            cards.append(card)
        same = [i for i in card["items"] if not i.get("done_at") and norm_text(i["text"]) == norm_text(text)]
        if same:
            return dict(same[0], card=card["id"], topic=label, existed=True)
        item = {"id": "i-" + secrets.token_hex(3), "text": scrub(text), "done_at": None, **meta}
        card["items"].append(item)
        return dict(item, card=card["id"], topic=label)


def find_open(cards: list[dict], kind: str, topic: str | None, text: str) -> tuple[dict, dict | None]:
    """The open item or reminder named by its id or by its exact text (and topic, if given)."""
    key = str(text or "").strip()
    found = []
    for c in cards:
        if c.get("archived") or c["kind"] != kind or (topic and c["topic"].lower() != topic.lower()):
            continue
        if kind == "reminder":
            if not c.get("done_at") and (c["id"] == key or norm_text(c["text"]) == norm_text(key)):
                found.append((c, None))
        else:
            found += [(c, i) for i in c["items"] if not i.get("done_at") and (i["id"] == key or norm_text(i["text"]) == norm_text(key))]
    if len(found) != 1:
        what = "No open" if not found else f"{len(found)} open"
        raise ToolError(f"{what} {kind} matches '{clip(key, 60)}'. Use companion_pending and pass the id it shows.")
    return found[0]


def close_card(scope: str, kind: str, topic: str | None, text: str, by: str = "user") -> dict:
    """Tick an open check item, or mark a reminder done."""
    label = topic_of(scope, topic)[0] if topic else None
    with card_store(scope, write=True) as cards:
        card, item = find_open(cards, kind, label, text)
        target = item or card
        target["done_at"], target["done_by"] = round(now(), 3), by
        return dict(target, topic=card["topic"], card=card["id"])


def set_item(scope: str, item_id: str, done: bool) -> dict:
    """The page's checkbox: tick or untick an item by id."""
    with card_store(scope, write=True) as cards:
        for c in cards:
            for i in c.get("items", []):
                if i["id"] == item_id:
                    i["done_at"], i["done_by"] = (round(now(), 3), "user") if done else (None, None)
                    return i
    raise ToolError(f"No item {item_id}.")


def update_card(scope: str, card_id: str, action: str, minutes: int = 0) -> dict:
    """The page's buttons on a card: done (a reminder), snooze (a reminder), archive (any card)."""
    if action not in ("done", "snooze", "archive"):
        raise ToolError("action is done, snooze or archive.")
    with card_store(scope, write=True) as cards:
        for c in cards:
            if c["id"] != card_id:
                continue
            if action == "archive":
                c["archived"] = round(now(), 3)
            elif c["kind"] != "reminder":
                raise ToolError("Only a reminder can be done or snoozed; tick a check card's items instead.")
            elif action == "done":
                c["done_at"], c["done_by"] = round(now(), 3), "user"
            else:
                if not 1 <= minutes <= 24 * 60:
                    raise ToolError("Snooze between 1 minute and 24 hours.")
                c["at"], c["fired_at"] = now() + minutes * 60, None
            return c
    raise ToolError(f"No card {card_id}.")


def today_start(t: float) -> float:
    return datetime.fromtimestamp(t).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def pending(scope: str) -> dict:
    """What the page and companion_pending show: open reminders by time, and each topic's open
    items plus the ones ticked today."""
    t = now()
    day = today_start(t)
    with card_store(scope) as cards:
        live = [c for c in cards if not c.get("archived")]
    reminders = sorted((c for c in live if c["kind"] == "reminder" and (not c.get("done_at") or c["done_at"] >= day)),
                       key=lambda c: (bool(c.get("done_at")), c["at"]))
    checks = []
    for c in live:
        if c["kind"] != "check":
            continue
        items = [i for i in c["items"] if not i.get("done_at") or i["done_at"] >= day]
        checks.append(dict(c, items=items, open=sum(not i.get("done_at") for i in items)))
    checks.sort(key=lambda c: (c["open"] == 0, c["topic"].lower()))
    return {"now": t, "reminders": [dict(r, due=r["at"] <= t and not r.get("done_at")) for r in reminders], "checks": checks}


def due_count() -> int:
    """Reminders whose time has come and that the user has not marked done, in every workspace."""
    t, n = now(), 0
    for f in (HOME / "cards").glob("*.json") if (HOME / "cards").is_dir() else []:
        n += sum(c["kind"] == "reminder" and not c.get("archived") and not c.get("done_at") and c["at"] <= t
                 for c in read_json(f, {"cards": []})["cards"])
    return n


def chat_file(scope: str) -> Path:
    """The page's chat (chat.py). A reminder that fires is written there too, so it stays in the
    conversation after it is snoozed or done."""
    return HOME / "chat" / f"{scope}.jsonl"


def chat_event(scope: str, event: dict) -> None:
    chat_file(scope).parent.mkdir(parents=True, exist_ok=True)
    with chat_file(scope).open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def fire_reminders() -> int:
    """Run by the daemon's poll: a desktop notification once per due reminder. No model."""
    fired, t = 0, now()
    for scope in card_scopes():
        if not cards_file(scope).is_file():
            continue
        with card_store(scope, write=True) as cards:
            for c in cards:
                if c["kind"] == "reminder" and not c.get("archived") and not c.get("done_at") and not c.get("fired_at") and c["at"] <= t:
                    c["fired_at"] = round(t, 3)
                    fired += 1
                    chat_event(scope, {"t": c["fired_at"], "who": "reminder", "id": c["id"]})
                    if not os.environ.get("TANKA_COMPANION_BACKTEST"):
                        tc.desktop_notify(f"⏰ {c['topic']} · {scope}", c["text"])
    return fired


def brief_time(hhmm_: str) -> tuple[int, int] | None:
    m = re.fullmatch(r"(\d\d):(\d\d)", hhmm_.strip())
    return (int(m[1]), int(m[2])) if m and int(m[1]) < 24 and int(m[2]) < 60 else None


def brief_of(cards: list[dict], t: float, stale_days: int) -> dict:
    """The day's brief: the open reminders due by tonight (overdue ones too), the open check items
    per topic, and the items open stale_days or more. Ids only: the chat shows each one as it is now."""
    tonight = today_start(t) + 86400
    live = [c for c in cards if not c.get("archived")]
    reminders = sorted((c for c in live if c["kind"] == "reminder" and not c.get("done_at") and c["at"] < tonight),
                       key=lambda c: c["at"])
    items = [(c, i) for c in live if c["kind"] == "check" for i in c.get("items", []) if not i.get("done_at")]
    topics: dict[str, int] = {}
    for c, _ in items:
        topics[c["topic"]] = topics.get(c["topic"], 0) + 1
    stale = [i["id"] for _, i in sorted(items, key=lambda x: x[1].get("t", t)) if t - i.get("t", t) >= stale_days * 86400]
    return {"reminders": [c["id"] for c in reminders], "topics": topics, "stale": stale}


def daily_brief() -> int:
    """Run by the daemon's poll: once a day, at the workspace's brief time, the companion says in the
    chat what the day holds and what has stalled. No model; nothing is said on a day with nothing open."""
    t, sent = now(), 0
    st = read_json(state_file(), {})
    said = st.setdefault("brief", {})
    today = datetime.fromtimestamp(t).strftime("%Y-%m-%d")
    for scope in card_scopes():
        if said.get(scope) == today or not cards_file(scope).is_file():
            continue
        try:
            cfg = load_config(ws_dir(scope))
        except (ToolError, ValueError, TypeError):
            continue
        hm_ = brief_time(cfg["brief"]) if cfg["brief"] else None
        days = cfg["stale_days"] if isinstance(cfg["stale_days"], int) and cfg["stale_days"] >= 1 else DEFAULT_STALE_DAYS
        if hm_ is None:
            continue
        at = datetime.fromtimestamp(t).replace(hour=hm_[0], minute=hm_[1], second=0, microsecond=0).timestamp()
        if t < at:
            continue
        said[scope] = today
        if t - at > BRIEF_LATE_HOURS * 3600:
            continue
        with card_store(scope) as cards:
            b = brief_of(cards, t, days)
        if not b["reminders"] and not b["topics"]:
            continue
        chat_event(scope, {"t": round(t, 3), "who": "brief", **b})
        sent += 1
        if not os.environ.get("TANKA_COMPANION_BACKTEST"):
            n_items = sum(b["topics"].values())
            tc.desktop_notify(f"Your day · {scope}", f"{len(b['reminders'])} reminder(s), {n_items} to do"
                              + (f", {len(b['stale'])} stalled" if b["stale"] else ""))
    write_json(state_file(), st)
    return sent


def rel(at: float, t: float) -> str:
    mins = -(-(at - t) // 60)  # ceiling: a reminder 20 s away is 'in 1 min', not 'in 0'
    mins = int(mins)
    if at <= t:
        return f"due since {hhmm(at)}" if -mins < 24 * 60 else f"due since {day_time(at)}"
    if mins < 60:
        return f"in {mins} min"
    return f"in {mins // 60} h {mins % 60:02d} min" if mins < 24 * 60 else f"in {mins // 1440} day(s)"


def list_pending(scope: str, topic: str | None) -> None:
    p, t = pending(scope), now()
    label = topic_of(scope, topic)[0] if topic else None
    rem = [r for r in p["reminders"] if not label or r["topic"].lower() == label.lower()]
    checks = [c for c in p["checks"] if not label or c["topic"].lower() == label.lower()]
    items = [(c, i) for c in checks for i in c["items"]]
    n_open = sum(not i.get("done_at") for _, i in items)
    print(f"Now {datetime.fromtimestamp(t):%Y-%m-%d %H:%M (%a)}. {sum(not r.get('done_at') for r in rem)} open reminder(s), "
          f"{n_open} open item(s) in {len(checks)} topic(s)" + (f" for '{label}'" if label else "") + ":")
    for r in rem:
        state = f"done {hhmm(r['done_at'])}" if r.get("done_at") else f"{day_time(r['at'])} ({rel(r['at'], t)})"
        print(f"{r['id']} reminder [{r['topic']}] {state}: {r['text']}" + ("  (added by you)" if r.get("by") == "companion" else ""))
    for c, i in items:
        state = f"done {hhmm(i['done_at'])}" if i.get("done_at") else "open"
        print(f"{i['id']} check [{c['topic']}] {state}: {i['text']}" + ("  (added by you)" if i.get("by") == "companion" else ""))
    if not rem and not items:
        print("Nothing pending." + (" Topics with cards: " + ", ".join(sorted({c['topic'] for c in p['checks']})) if label and p["checks"] else ""))


def card_tool(scope: str, kind: str, text: str, topic: str | None, at: str | None, done: bool, evidence: str) -> None:
    if done:
        x = close_card(scope, kind, topic, text, by="companion")
        what = "Ticked" if kind == "check" else "Marked done"
        print(f"{what} {x['id']} [{x['topic']}]: {x['text']}. Tell the user in one line; do not call it again.")
        return
    x = add_card(scope, kind, topic, text, at, by="companion", evidence=evidence)
    if x.get("existed"):
        print(f"Already there: {x['id']} [{x['topic']}] {x['text']}. Nothing added; do not call it again.")
    elif kind == "reminder":
        print(f"Reminder {x['id']} [{x['topic']}] for {day_time(x['at'])} ({rel(x['at'], now())}): {x['text']}. "
              "The user gets a desktop notification then, and sees it in the page. Do not call it again.")
    else:
        print(f"Added {x['id']} to the [{x['topic']}] check card: {x['text']}. It shows in the page. Do not call it again.")


# ---------------------------------------------------------------- backtest: past sessions, nothing sent

def claude_project_dir(project: str) -> Path:
    return CLAUDE_HOME / "projects" / re.sub(r"[^a-zA-Z0-9]", "-", project)


def iso(ts) -> float:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def transcript_events(path: Path, project: str) -> list[dict]:
    """A past Claude Code transcript as feed events. The format is internal, so this is tolerant and used only to backtest."""
    sid, out, pending = safe_id(path.stem), [], {}

    def add(t, ev):
        out.append({"t": t, "session": sid, "project": project, **ev})

    for rec in read_feed(path):
        if rec.get("isSidechain") or rec.get("isMeta") or rec.get("isCompactSummary"):
            continue
        t, msg = iso(rec.get("timestamp")), rec.get("message") if isinstance(rec.get("message"), dict) else {}
        if not t:
            continue
        if not out:
            add(t, {"e": "session_start", "source": "backtest", "branch": str(rec.get("gitBranch") or "")})
        content = msg.get("content")
        if rec.get("type") == "user":
            if isinstance(content, str) and not content.startswith("<"):
                add(t, {"e": "prompt", "text": clip(content, PROMPT_CHARS)})
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and not str(b.get("text", "")).startswith("<"):
                    add(t, {"e": "prompt", "text": clip(b.get("text"), PROMPT_CHARS)})
                elif b.get("type") == "tool_result" and b.get("tool_use_id") in pending:
                    name, ti = pending.pop(b["tool_use_id"])
                    res = b.get("content")
                    res = " ".join(x.get("text", "") for x in res if isinstance(x, dict)) if isinstance(res, list) else res
                    add(t, tool_event({"tool_name": name, "tool_input": ti, "error": res}, not b.get("is_error")))
        elif rec.get("type") == "assistant":
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use":
                    pending[b.get("id")] = (b.get("name"), b.get("input") if isinstance(b.get("input"), dict) else {})
            if msg.get("stop_reason") == "end_turn":
                add(t, {"e": "turn_end"})
    return out


def past_sessions(scope: str, days: int) -> list[tuple[str, list[dict]]]:
    """(project, events) for every transcript of this workspace's projects touched in the last `days`."""
    since, out = now() - days * 86400, []
    for project in projects_of(scope):
        for f in sorted(claude_project_dir(project).glob("*.jsonl")):
            if f.stat().st_mtime >= since:
                out.append((project, transcript_events(f, project)))
    return out


def history(ws: Path, days: int) -> int:
    """What the catalog signals would have caught in the user's past sessions, before any lens exists."""
    scope, th = ws.name, load_config(ws)["thresholds"]
    past = past_sessions(scope, days)
    if not past:
        print(f"No past sessions in {days} day(s) for the projects of '{scope}' "
              f"({', '.join(projects_of(scope)) or 'none watched'}).")
        return 1
    by = {s: [] for s in SIGNALS}
    for project, evs in past:
        for f in firings(evs, th) + idle_firings(evs, th):
            by[f["signal"]].append((f, evs[0]["session"], project))
    print(f"History of '{scope}': {len(past)} session(s) over {days} day(s), thresholds {json.dumps(th)}.")
    for sig, hits in by.items():
        print(f"\n{sig}: {len(hits)} time(s), ~{len(hits) / max(days, 1):.1f} per day - {SIGNALS[sig]}")
        for f, sid, project in hits[-3:]:
            print(f"  {day_time(f['t'])} {sid[:8]} {Path(project).name}: {f['evidence']}")
    return 0


def backtest(ws: Path, days: int, say: int, runner=None) -> int:
    scope = ws.name
    problems, _ = check(ws)
    if problems:
        for p in problems:
            print(f"x {p}")
        print("Fix these first: a backtest runs the companion as it would run live.")
        return 1
    cfg = load_config(ws)
    th, since, hits = cfg["thresholds"], now() - days * 86400, []
    for project, evs in past_sessions(scope, days):
        for fire in firings(evs, th) + idle_firings(evs, th):
            lenses = awake(ws, fire["signal"])
            if lenses and fire["t"] >= since and not quiet(cfg["quiet_hours"], fire["t"]):
                hits.append((fire, lenses, evs, project))
    hits.sort(key=lambda h: h[0]["t"])
    per_day = len(hits) / max(days, 1)
    print(f"Backtest of '{scope}' over {days} day(s), {len(projects_of(scope))} project(s): {len(hits)} wake-up(s), "
          f"~{per_day:.1f} per day, before the model decides whether to speak. Nothing is sent.")
    for fire, lenses, evs, project in hits:
        print(f"{day_time(fire['t'])} | {evs[0]['session'][:8]} | {Path(project).name} | {fire['signal']} | {','.join(lenses)} | {fire['evidence']}")
    if hits and say:
        step = max(1, len(hits) // say)
        print(f"\nWhat the companion would have said, for {min(say, len(hits))} of them (each one is a real model call):")
        for fire, lenses, evs, project in hits[::step][:say]:
            print(f"\n{day_time(fire['t'])} {fire['signal']}: " + say_one(ws, fire, lenses, evs, project, runner))
    return 0


def say_one(ws: Path, fire: dict, lenses: list[str], evs: list[dict], project: str, runner=None) -> str:
    import shutil
    import tempfile
    sys.path.insert(0, str(REPO / "plugin" / "scripts"))
    import tanka_automation as ta
    tmp = Path(tempfile.mkdtemp(prefix="companion-backtest-"))
    try:
        (tmp / "feed").mkdir()
        write_json(tmp / "watch.json", {"projects": {project: ws.name}})
        with (tmp / "feed" / f"{evs[0]['session']}.jsonl").open("w", encoding="utf-8") as fh:
            for ev in evs[: fire["i"] + 1]:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        env = {**os.environ, "TANKA_COMPANION_HOME": str(tmp), "TANKA_COMPANION_BACKTEST": "1",
               "TANKA_COMPANION_RECENT_HOURS": str(10 ** 6)}
        task = "React to the signal with your lenses (skill companion). This is a backtest: speak with companion_note exactly as you would live."
        cmd = [str(REPO / "bin" / "tanka"), "run", ta.prompt(task, event_for(ws.name, evs[0]["session"], project, fire, lenses)["what"]),
               str(ws), "--max-turns", "10", "--budget", "0.15"]
        runner = runner or (lambda c: subprocess.run(c, capture_output=True, text=True, timeout=600, env=env, stdin=subprocess.DEVNULL))
        p = runner(cmd)
        said = read_feed(tmp / "notes" / f"{safe_id(ws.name)}.jsonl")
        if said:
            return " | ".join(f"[{n['lens']}] {n['text']}" for n in said)
        return "(stays quiet)" if p.returncode == 0 else f"(the run failed: {clip(p.stderr or p.stdout, 200)})"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
