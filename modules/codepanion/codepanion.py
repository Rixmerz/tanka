"""Codepanion for Tanka: a counterpart that watches the user's Claude Code sessions and speaks first.

The module ships the mechanism; each user builds the role (docs/codepanion.md):

- The **tap** (`tap.py`, hooks in the user's normal Claude Code) appends one
  compact event per hook to `feed/<session>.jsonl`, only for projects listed in
  `watch.json`, with secrets scrubbed before anything is written.
- `events()` turns the feed into **signals** (deterministic, no model) and
  hands the automation daemon only those that wake one of the workspace's
  **lenses** (`.claude/codepanion/lenses/*.md`, written with `tanka dev`).
- The tools read the feed and the watched repository, and queue **notes**
  for the user. Nothing here writes to a watched project.

What each workspace may see is decided by `watch.json` (in TANKA_CODEPANION_HOME,
outside every workspace): a project belongs to exactly one workspace, and
every tool refuses sessions and paths that belong to another.

Standard library only.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path

MODULE = Path(__file__).resolve().parent
REPO = MODULE.parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
sys.path.insert(0, str(REPO / "modules" / "desk"))
import desk  # noqa: E402  (session close puts leftovers on the user's cards)
import tanka_common as tc  # noqa: E402
import tanka_kit as kit  # noqa: E402
from tanka_kit import (SECRET_PATTERNS, ToolError, clip, day_time, hhmm, now, persona_name, read_json,  # noqa: E402,F401
                       run, safe_id, scrub, tail_clip, today_start, write_json)

# Every setting has a default and an environment variable; see README.md.
HOME = Path(os.environ.get("TANKA_CODEPANION_HOME", Path.home() / ".tanka" / "shared" / "codepanion"))
CLAUDE_HOME = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude"))
FRESH_SECONDS = int(os.environ.get("TANKA_CODEPANION_FRESH_SECONDS", "600"))
RECENT_HOURS = int(os.environ.get("TANKA_CODEPANION_RECENT_HOURS", "24"))

PROMPT_CHARS, TARGET_CHARS, ERROR_CHARS = 500, 120, 300
DIGEST_CHARS = 5500  # under the 6000 the harness allows, with room for the header
MAX_LENSES = 3
SIGNALS = {
    "turn_end_substantial": "a turn that changed many lines or files",
    "stuck": "the same failure repeating in one session",
    "idle_dirty": "a session gone quiet with uncommitted changes",
    "session_closed": "a session that ended, or went quiet for hours",
}
CLOSE_IDLE_HOURS = 3      # a session with no SessionEnd counts as closed after this long quiet
DEFAULT_THRESHOLDS = {"turn_lines": 30, "turn_files": 3, "stuck": 3, "idle_minutes": 20}
DEFAULT_BUDGET = {"runs_per_hour": 6, "notes_per_hour": 3}
MAX_BUDGET = {"runs_per_hour": 12, "notes_per_hour": 6}
SPEAKS = ("question", "finding", "reminder")
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"

# ---------------------------------------------------------------- scope: watch.json

def watch_file() -> Path:
    return HOME / "watch.json"


def real(path) -> str:
    return str(Path(os.path.expanduser(str(path))).resolve())


def watch() -> dict[str, str]:
    """{real project path: workspace name}. Only the user edits it (`tanka codepanion watch`)."""
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
    if name == "Bash":
        # The target is clipped, and a push or commit late in a long command would fall off its end:
        # keep those git calls on their own so the guardian sees them. Always set, so an empty list
        # says "no git call here" rather than "an older feed line": then the target is not searched.
        calls = [m.group(0) for m in GIT_CALL_RE.finditer(shell_code(str(ti.get("command") or "")))][:3]
        ev["git"] = [clip(x, TARGET_CHARS) for x in calls]
    if name == "TodoWrite" and isinstance(ti.get("todos"), list):
        ev["todos"] = [{"text": clip(scrub(td.get("content") or ""), TARGET_CHARS), "status": str(td.get("status") or "")}
                       for td in ti["todos"][:20] if isinstance(td, dict) and td.get("content")]
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
        raise ToolError("No watched session yet. The user adds projects with `tanka codepanion watch add <path> <workspace>`; tell them.")
    if not session:
        sid = next(iter(sessions))
        return sid, sessions[sid]
    hits = [s for s in sessions if s.startswith(safe_id(session))]
    if len(hits) != 1:
        raise ToolError(f"No single watched session starts with '{session}'. Use codepanion_sessions to see the ids.")
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


def closed_at(events: list[dict], until: float | None = None) -> float | None:
    """When the session ended: its SessionEnd, or (live) CLOSE_IDLE_HOURS after its last event."""
    if not events:
        return None
    if events[-1].get("e") == "session_end":
        return events[-1].get("t", 0)
    last = events[-1].get("t", 0)
    return last + CLOSE_IDLE_HOURS * 3600 if until is not None and until - last >= CLOSE_IDLE_HOURS * 3600 else None


def close_firings(events: list[dict], until: float | None = None) -> list[dict]:
    t = closed_at(events, until)
    if t is None:
        return []
    edits = sum(1 for e in events if e.get("e") == "tool" and e.get("tool") in EDIT_TOOLS and e.get("ok"))
    return [{"signal": "session_closed", "key": "closed", "i": len(events) - 1, "t": t,
             "evidence": f"the session ended at {hhmm(t)} after {len(events)} events and {edits} edit(s)"}]


# ---------------------------------------------------------------- the user's codepanion: config and lenses

def ws_dir(scope: str) -> Path:
    return kit.ws_dir(scope)


def config_file(ws: Path) -> Path:
    return ws / ".claude" / "codepanion.json"


def lenses_dir(ws: Path) -> Path:
    return ws / ".claude" / "codepanion" / "lenses"


def load_config(ws: Path) -> dict:
    raw = read_json(config_file(ws), {})
    return {"lenses": list(raw.get("lenses") or []), "proactivity": int(raw.get("proactivity", 1)),
            "notify": bool(raw.get("notify", False)), "quiet_hours": str(raw.get("quiet_hours") or ""),
            "budget": {**DEFAULT_BUDGET, **(raw.get("budget") or {})},
            "thresholds": {**DEFAULT_THRESHOLDS, **(raw.get("thresholds") or {})},
            "guardian": bool(raw.get("guardian", True)), "session_close": bool(raw.get("session_close", True))}


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
    """(problems, warnings) for a workspace's codepanion; no problems means it may run."""
    problems, warnings = [], []
    if not config_file(ws).is_file():
        return [f"{config_file(ws)} does not exist: run `tanka install codepanion <workspace>` first"], []
    try:
        cfg = load_config(ws)
    except (ToolError, ValueError, TypeError) as e:
        return [str(e)], []
    lenses = load_lenses(ws)
    active = cfg["lenses"]
    if not active:
        warnings.append("no lens yet: only the built-in checks run; build one with /new-codepanion in `tanka dev <workspace>`")
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
    for k in DEFAULT_THRESHOLDS:
        if not isinstance(cfg["thresholds"].get(k), int) or cfg["thresholds"][k] < 1:
            problems.append(f"thresholds.{k} must be a positive integer")
    if not projects_of(ws.name):
        warnings.append(f"no project is watched for '{ws.name}': tanka codepanion watch add <path> {ws.name}")
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


# ---------------------------------------------------------------- built in: the guardian and the session close
#
# Two things every codepanion does from the moment it is installed, with no lens and no model: the
# guardian calls out a force push, a secret added to the tree or a commit, and a commit made under
# an identity the repository does not usually see; the session close turns what a session left
# behind (uncommitted files, Claude's unfinished todos) into items on the project's card. Lenses are
# for judgment; these are facts, so they are code.

PUSH_RE = re.compile(r"\bgit\b[^|;&]*?\bpush\b([^|;&]*)")
FORCE_RE = re.compile(r"(?:^|\s)(?:-f|--force|--force-with-lease)(?:=\S*)?(?=\s|$)")
COMMIT_RE = re.compile(r"\bgit\b[^|;&]*?\bcommit\b")
GIT_CALL_RE = re.compile(r"\bgit\b[^|;&]*?\b(?:push|commit)\b[^|;&]*")
HEREDOC_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2[ \t]*(?:\n|$)", re.S)
QUOTED_RE = re.compile(r"'[^']*'|\"(?:[^\"\\]|\\.)*\"")


def shell_code(cmd: str) -> str:
    """A command without its heredoc bodies and quoted strings: text a command carries (a file it
    writes, a commit message) is not a command it runs."""
    return QUOTED_RE.sub("''", HEREDOC_RE.sub("\n", cmd))
PLACEHOLDER_RE = re.compile(r"[{}<>$%\[(]")  # a template, a lookup or a call: not a value
ASSIGNED_RE = re.compile(r"[:=]\s*(['\"]?)(\S+)$")


def secret_shaped(found: str) -> bool:
    """Whether a secret-pattern match is a value, not code that names one (`token = settings.TOKEN`)."""
    if PLACEHOLDER_RE.search(found):
        return False
    m = ASSIGNED_RE.search(found)
    value = (m.group(2) if m else found.split()[-1]).strip("`'\").,;:")
    if m and not m.group(1) and re.fullmatch(r"[A-Za-z_]+|[A-Za-z_]\w*(?:\.\w+)+", value):
        return False  # a name in code
    # "__TOKEN__", "YOUR_API_KEY", "xxxxxxxx": a placeholder written to be replaced
    return not (re.fullmatch(r"_*[A-Z][A-Z0-9_]*", value) or len(set(value)) <= 3)
CLOSE_FRESH_HOURS = 12   # a session that closed longer ago than this is marked, not reported
MAX_CLOSE_ITEMS = 5
COMMIT_WINDOW = 600      # a commit this much older than the command that "made" it was not made by it

SAYS = {
    "en": {"force_push": "A force push in {project}: `{cmd}`. It rewrites the history others may have pulled.",
           "secret_commit": "The last commit in {project} adds what looks like a secret: {where}.",
           "secret_tree": "Your uncommitted changes in {project} add what looks like a secret: {where}.",
           "identity": "The last commit in {project} is signed as {email}; your earlier commits there use {usual}.",
           "dirty": "Session {sid} ended with {n} uncommitted file(s): {files}",
           "todo": "Left open in session {sid}: {text}"},
    "es": {"force_push": "Un push forzado en {project}: `{cmd}`. Reescribe historia que otros pueden haber bajado.",
           "secret_commit": "El último commit en {project} agrega algo que parece un secreto: {where}.",
           "secret_tree": "Tus cambios sin commit en {project} agregan algo que parece un secreto: {where}.",
           "identity": "El último commit en {project} va firmado como {email}; tus commits anteriores ahí usan {usual}.",
           "dirty": "La sesión {sid} terminó con {n} archivo(s) sin commit: {files}",
           "todo": "Quedó abierto en la sesión {sid}: {text}"},
}


def say(ws: Path, key: str, **kw) -> str:
    lang = kit.persona_language(ws)
    return SAYS.get(lang, SAYS["en"])[key].format(**kw)


def secret_spots(diff_text: str) -> list[str]:
    """file:line of every added line that matches a secret pattern; never the secret itself."""
    spots, path, line = [], "", 0
    for raw in diff_text.splitlines():
        if raw.startswith("+++ "):
            path = raw[6:] if raw.startswith("+++ b/") else raw[4:]
        elif raw.startswith("@@"):
            m = re.search(r"\+(\d+)", raw)
            line = int(m.group(1)) if m else 0
        elif raw.startswith("+") and not raw.startswith("+++"):
            if any(secret_shaped(m.group(0)) for p in SECRET_PATTERNS for m in p.finditer(raw[1:])):
                spots.append(f"{path}:{line}")
            line += 1
        elif not raw.startswith("-"):
            line += 1
    return spots[:3]


def guardian_findings(evs: list[dict], start: int, repo: str, ws: Path) -> list[tuple[str, str, str]]:
    """(key, text, evidence) for each fact worth saying in events[start:]. Runs git read-only."""
    project = Path(repo).name
    out, seen = [], set()
    for i in range(start, len(evs)):
        ev = evs[i]
        target = str(ev.get("target") or "")
        if ev.get("e") == "tool" and ev.get("tool") == "Bash" and ev.get("ok"):
            calls = ev["git"] if "git" in ev else [target]  # feed lines from before "git" was kept
            push = next((m for m in map(PUSH_RE.search, calls) if m and FORCE_RE.search(m.group(1))), None)
            if push:
                cmd = re.sub(r"\s*\d*>+\s*$", "", push.group(0).strip())  # the cut of a `2>&1`
                out.append((f"push:{i}", say(ws, "force_push", project=project, cmd=clip(cmd, 80)), f"Bash: {cmd}"))
            if any(COMMIT_RE.search(x) for x in calls):
                try:
                    sha, _, made = safe_git(repo, ["log", "-1", "--format=%H %ct"]).strip().partition(" ")
                    # Only a commit this command made in this repo: `git -C elsewhere commit`, a failed commit,
                    # or "git commit" inside a string leave an older HEAD behind.
                    if sha in seen or not ev.get("t", 0) - COMMIT_WINDOW <= int(made or 0) <= ev.get("t", 0) + 5:
                        continue
                    seen.add(sha)
                    shown = safe_git(repo, ["show", "--format=", "--unified=0", "--no-ext-diff", "--no-textconv", sha])
                    who = safe_git(repo, ["log", "-1", "--format=%an|%ae", sha]).strip()
                    earlier = safe_git(repo, ["log", "--skip=1", "-50", "--format=%an|%ae", sha]).splitlines()
                except (ToolError, subprocess.SubprocessError, ValueError):
                    continue
                spots = secret_spots(shown)
                if spots:
                    out.append((f"secret-commit:{sha}", say(ws, "secret_commit", project=project, where=", ".join(spots)),
                                f"git show {sha[:8]}"))
                name, _, email = who.partition("|")
                mine = [e.partition("|")[2] for e in earlier if e.partition("|")[0] == name]
                if email and len(mine) >= 5 and email not in mine:
                    usual = max(set(mine), key=mine.count)
                    out.append((f"identity:{sha}", say(ws, "identity", project=project, email=email, usual=usual),
                                f"git log {sha[:8]}: {email}, {len(mine)} earlier ones by {name}"))
        elif ev.get("e") == "turn_end":
            try:
                spots = secret_spots(safe_git(repo, ["diff", "HEAD", "--unified=0", "--no-ext-diff", "--no-textconv"]))
            except (ToolError, subprocess.SubprocessError):
                continue
            if spots:
                out.append(("secret-tree:" + ",".join(spots), say(ws, "secret_tree", project=project, where=", ".join(spots)),
                            "git diff HEAD after the turn"))
    return out


def close_session(scope: str, ws: Path, sid: str, evs: list[dict]) -> int:
    """Put what the session left behind on the project's card; returns how many items were added."""
    repo, added = repo_of(evs), 0
    project = evs[0].get("project", "")
    todos = next((e["todos"] for e in reversed(evs) if e.get("e") == "tool" and e.get("todos") is not None), [])
    things = []
    try:
        changed = [ln[3:] for ln in safe_git(repo, ["status", "--porcelain", "--ignore-submodules=all"]).splitlines() if ln.strip()]
    except (ToolError, subprocess.SubprocessError):
        changed = []
    if changed:
        files = ", ".join(changed[:4]) + (f" (+{len(changed) - 4})" if len(changed) > 4 else "")
        things.append((say(ws, "dirty", sid=sid[:8], n=len(changed), files=files), f"git status in {repo}"))
    for td in [t for t in todos if t.get("status") != "completed"][:MAX_CLOSE_ITEMS]:
        things.append((say(ws, "todo", sid=sid[:8], text=td["text"]), "the session's last TodoWrite"))
    for text, evidence in things:
        try:
            desk.add_card(scope, "check", Path(project).name or None, clip(text, desk.CARD_CHARS), by="codepanion-close",
                          evidence=evidence, project=project)
            added += 1
        except ToolError:
            continue
    return added


def builtins(scope: str, ws: Path, cfg: dict, st: dict, t: float) -> None:
    """The guardian and the session close for one codepanion, on every daemon poll."""
    guard, closed = st.setdefault("guard", {}), st.setdefault("closed", {})
    for sid, evs in sessions_of(scope).items():
        if cfg["guardian"]:
            start = guard.get(sid)
            if start is None:  # first sight: only what is fresh, never a session's whole past
                start = next((i for i, e in enumerate(evs) if t - e.get("t", 0) <= FRESH_SECONDS), len(evs))
            if start < len(evs):
                for _, text, evidence in guardian_findings(evs, start, repo_of(evs), ws):
                    write_note(scope, "guardian", text, evidence, sid, True)
            guard[sid] = len(evs)
        when = closed_at(evs, until=t)
        if when is not None and sid not in closed:
            closed[sid] = when
            if cfg["session_close"] and t - when <= CLOSE_FRESH_HOURS * 3600:
                close_session(scope, ws, sid, evs)


# ---------------------------------------------------------------- events for the automation daemon

def state_file() -> Path:
    return HOME / "state.json"


def event_for(scope: str, sid: str, project: str, f: dict, lenses: list[str]) -> dict:
    return {"source": "codepanion", "scope": scope, "match": f["signal"], "signal": f["signal"], "session": sid,
            "project": project, "at": f["t"],
            "what": (f"the signal {f['signal']} in session {sid[:8]} of {project}: {f['evidence']}. "
                     f"Lenses awake: {', '.join(lenses)}. Start with codepanion_digest session={sid[:8]} signal={f['signal']}.")}


def repo_of(evs: list[dict]) -> str:
    """Where git runs for a session: the directory it started in, when that is inside its watched project."""
    project = evs[0].get("project", "")
    cwd = next((e.get("cwd") for e in evs if e.get("e") == "session_start" and e.get("cwd")), "")
    return cwd if cwd == project or cwd.startswith(project + os.sep) else project


def dirty(project: str) -> bool:
    return bool(git_out(project, "status", "--porcelain", "--ignore-submodules=all", timeout=5))


def events() -> list[dict]:
    """New signals that wake a lens, one dict per line for `tanka codepanion events`. No prompt text."""
    st = read_json(state_file(), {})
    emitted, runs, out, t = st.setdefault("emitted", {}), st.setdefault("runs", {}), [], now()
    for scope in sorted(set(watch().values())):
        ws = ws_dir(scope)
        if config_file(ws).is_file():
            try:
                cfg = load_config(ws)
                if cfg["proactivity"]:
                    builtins(scope, ws, cfg, st, t)
            except (ToolError, ValueError, TypeError, OSError):
                pass  # a broken built-in never stops the lenses
        try:
            problems, _ = check(ws)
        except ToolError:
            continue
        if problems:
            continue  # a codepanion that does not pass check never runs
        cfg = load_config(ws)
        if cfg["proactivity"] == 0:
            continue
        recent = [x for x in runs.get(scope, []) if t - x < 3600]
        for sid, evs in sessions_of(scope).items():
            done = set(emitted.get(sid, []))
            project = evs[0].get("project", "")
            for f in firings(evs, cfg["thresholds"]) + idle_firings(evs, cfg["thresholds"], until=t) + close_firings(evs, until=t):
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
        raise ToolError("This codepanion watches no project yet. Tell the user to run `tanka codepanion watch add <path> <workspace>`.")
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
    if os.environ.get("TANKA_CODEPANION_BACKTEST"):
        raise ToolError("This is a backtest: the repository today is not what it was then, so there is no diff. Judge from the digest.")
    sid, evs = session_of(scope, session)
    project = evs[0].get("project", "")
    if project not in projects_of(scope):
        raise ToolError("That session's project is not watched by this codepanion. Tell the user.")
    repo = repo_of(evs)
    out = scrub(safe_git(repo, VIEWS[view])).splitlines()
    print(f"git {view} of {repo} (session {sid[:8]}), {len(out)} line(s){', first ' + str(max_lines) if len(out) > max_lines else ''}:")
    print("\n".join(out[:max_lines]) if out else "(nothing)")


# ---------------------------------------------------------------- notes: how the codepanion speaks

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
        raise ToolError(f"'{lens}' is not an active lens of this codepanion (active: {', '.join(cfg['lenses']) or 'none'}). Say nothing.")
    if len(evidence.strip()) < 8:
        raise ToolError("A note needs its evidence: the event, file or command it is about. Without it, say nothing.")
    if lenses[lens]["meta"].get("speaks") == "question" and "?" not in text:
        raise ToolError(f"The lens {lens} only asks questions. Rewrite the note as a question, or say nothing.")
    sid = session_of(scope, session)[0] if session else ""
    t, notes = now(), read_notes(scope)
    if any(n.get("hash") == claim_hash(lens, text, evidence) for n in notes):
        raise ToolError("This was already said to the user. Do not say it again; say nothing.")
    if sum(t - n.get("t", 0) < 3600 for n in notes) >= cfg["budget"]["notes_per_hour"]:
        raise ToolError("The note budget for this hour is spent. Say nothing now.")
    n = write_note(scope, lens, text, evidence, sid, cfg["notify"])
    print(f"Noted {n['id']} for the user ({lens}). It shows in their status line and in `tanka codepanion notes`. "
          "Do not repeat it; end the run.")


def write_note(scope: str, lens: str, text: str, evidence: str, sid: str, notify: bool) -> dict | None:
    """Append one note, unless the same claim was already made; returns it, or None for a repeat."""
    h = claim_hash(lens, text, evidence)
    if any(n.get("hash") == h for n in read_notes(scope)):
        return None
    t = now()
    n = {"id": hashlib.sha256(f"{t}{text}".encode()).hexdigest()[:6], "t": round(t, 3), "lens": lens,
         "text": clip(text, 400), "evidence": clip(evidence, 300), "session": sid, "hash": h, "seen": False}
    notes_file(scope).parent.mkdir(parents=True, exist_ok=True)
    with notes_file(scope).open("a", encoding="utf-8") as f:
        f.write(json.dumps(n, ensure_ascii=False) + "\n")
    if notify and not os.environ.get("TANKA_CODEPANION_BACKTEST"):
        tc.desktop_notify(f"{persona_name(ws_dir(scope))} · {lens}", n["text"])
    return n


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
                if verdict == "bad":
                    suggest_tuning(f.stem, n["lens"])
                return dict(n, scope=f.stem)
    raise ToolError(f"No note {note_id}. See them with: tanka codepanion notes --all")


# ---------------------------------------------------------------- tuning: what a 👎 streak proposes

BAD_STREAK = 2  # this many 👎 in a row on one lens and the codepanion proposes to speak less
STRICTER = {"turn_end_substantial": ("turn_lines", "turn_files"), "stuck": ("stuck",), "idle_dirty": ("idle_minutes",)}
LENS_ACTIONS = ("stricter", "pause", "resume")


def tuning_file(scope: str) -> Path:
    return HOME / "tuning" / f"{safe_id(scope)}.jsonl"


def suggestions(scope: str) -> list[dict]:
    """Every proposal for this codepanion with its latest state (open, stricter, pause, resume)."""
    out: dict[str, dict] = {}
    for r in read_feed(tuning_file(scope)):
        out[r["id"]] = {**out.get(r["id"], {}), **r}
    return sorted(out.values(), key=lambda r: r.get("t", 0))


def suggest_tuning(scope: str, lens: str) -> dict | None:
    """After a 👎: if the lens's last BAD_STREAK verdicts are all bad, propose once to make it quieter."""
    mine = [r["verdict"] for r in read_feed(HOME / "feedback.jsonl") if r.get("scope") == scope and r.get("lens") == lens]
    if len(mine) < BAD_STREAK or any(v != "bad" for v in mine[-BAD_STREAK:]):
        return None
    if any(r["lens"] == lens and r.get("state") == "open" for r in suggestions(scope)):
        return None
    r = {"id": "tune-" + secrets.token_hex(3), "t": round(now(), 3), "lens": lens, "state": "open",
         "tunable": lens == "guardian" or any(s in STRICTER for s in load_lenses(ws_dir(scope)).get(lens, {}).get("meta", {}).get("wakes_on", []))}
    tuning_file(scope).parent.mkdir(parents=True, exist_ok=True)
    with tuning_file(scope).open("a", encoding="utf-8") as f:
        f.write(json.dumps(r) + "\n")
    return r


def lens_action(scope: str, lens: str, action: str) -> str:
    """Make a lens stricter, pause it or resume it, as the user chose; returns what changed."""
    if action not in LENS_ACTIONS:
        raise ToolError(f"The action is {', '.join(LENS_ACTIONS)}.")
    ws = ws_dir(scope)
    raw = read_json(config_file(ws), {})
    lenses = list(raw.get("lenses") or [])
    if lens == "guardian":
        if action == "stricter":
            raise ToolError("The guardian has no threshold: pause it, or keep it.")
        raw["guardian"] = action == "resume"
        done = f"The guardian is {'on' if raw['guardian'] else 'paused'}."
    elif action == "pause":
        raw["lenses"] = [x for x in lenses if x != lens]
        done = f"{lens} is paused; its file stays, resume it with: tanka codepanion lens {scope} {lens} resume"
    elif action == "resume":
        if lens not in load_lenses(ws):
            raise ToolError(f"There is no lens {lens} in {lenses_dir(ws)}.")
        if lens not in lenses and len(lenses) >= MAX_LENSES:
            raise ToolError(f"{scope} already has {MAX_LENSES} active lenses: pause one first.")
        raw["lenses"] = lenses if lens in lenses else lenses + [lens]
        done = f"{lens} is active again."
    else:
        wakes = load_lenses(ws).get(lens, {}).get("meta", {}).get("wakes_on", [])
        keys = [k for sig in wakes for k in STRICTER.get(sig, ())]
        if not keys:
            raise ToolError(f"{lens} wakes on {', '.join(wakes) or 'nothing'}, which has no threshold: sharpen its rubric, or pause it.")
        th = {**DEFAULT_THRESHOLDS, **(raw.get("thresholds") or {})}
        for k in keys:
            th[k] = max(th[k] + 1, -(-th[k] * 3 // 2))  # half again, at least one more
        raw["thresholds"] = th
        done = f"{lens} wakes less often now: " + ", ".join(f"{k} {th[k]}" for k in keys) + "."
    write_json(config_file(ws), raw)
    for r in suggestions(scope):
        if r["lens"] == lens and r.get("state") == "open":
            with tuning_file(scope).open("a", encoding="utf-8") as f:
                f.write(json.dumps({"id": r["id"], "state": action, "done": done}) + "\n")
    return done


def verdicts() -> dict[str, str]:
    """{note id: the user's latest verdict}."""
    return {r["id"]: r["verdict"] for r in read_feed(HOME / "feedback.jsonl") if r.get("id")}


def unseen() -> int:
    d = HOME / "notes"
    return sum(not n.get("seen") for f in (d.glob("*.jsonl") if d.is_dir() else []) for n in read_feed(f))


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
        for f in firings(evs, th) + idle_firings(evs, th) + close_firings(evs, until=now()):
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
        print("Fix these first: a backtest runs the codepanion as it would run live.")
        return 1
    cfg = load_config(ws)
    th, since, hits = cfg["thresholds"], now() - days * 86400, []
    for project, evs in past_sessions(scope, days):
        for fire in firings(evs, th) + idle_firings(evs, th) + close_firings(evs, until=now()):
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
        print(f"\nWhat the codepanion would have said, for {min(say, len(hits))} of them (each one is a real model call):")
        for fire, lenses, evs, project in hits[::step][:say]:
            print(f"\n{day_time(fire['t'])} {fire['signal']}: " + say_one(ws, fire, lenses, evs, project, runner))
    return 0


def say_one(ws: Path, fire: dict, lenses: list[str], evs: list[dict], project: str, runner=None) -> str:
    import shutil
    import tempfile
    sys.path.insert(0, str(REPO / "plugin" / "scripts"))
    import tanka_automation as ta
    tmp = Path(tempfile.mkdtemp(prefix="codepanion-backtest-"))
    try:
        (tmp / "feed").mkdir()
        write_json(tmp / "watch.json", {"projects": {project: ws.name}})
        with (tmp / "feed" / f"{evs[0]['session']}.jsonl").open("w", encoding="utf-8") as fh:
            for ev in evs[: fire["i"] + 1]:
                fh.write(json.dumps(ev, ensure_ascii=False) + "\n")
        env = {**os.environ, "TANKA_CODEPANION_HOME": str(tmp), "TANKA_CODEPANION_BACKTEST": "1",
               "TANKA_CODEPANION_RECENT_HOURS": str(10 ** 6)}
        task = "React to the signal with your lenses (skill codepanion). This is a backtest: speak with codepanion_note exactly as you would live."
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
