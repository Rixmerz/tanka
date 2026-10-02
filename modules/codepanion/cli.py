#!/usr/bin/env python3
"""`tanka codepanion …`: the human side of the codepanion module.

setup <workspace> <project> [--name NAME] [--language LANG] [--no-service]
                     all of it at once: workspace and its persona, module, watch, tap, trigger, daemon, then a self-test
watch add <path> <workspace> | remove <path> | list
                     which projects each codepanion may see (watch.json)
tap install|remove|status
                     the observe-only hooks in your normal Claude Code (~/.claude/settings.json)
check <workspace>    the rules every codepanion must pass before it runs
history <workspace> [--since 14d]
                     what the signals would have caught in your past sessions (no lenses needed)
backtest <workspace> [--since 14d] [--say N]
                     replay your past sessions through its lenses; nothing is sent
notes [workspace] [--all]
                     what it said; shown notes are marked seen
rate <id> good|bad   tell it whether a note was worth it; two 👎 in a row on one lens propose to quiet it
lens <workspace> <lens> stricter|pause|resume
                     wake a lens less often, pause it, or bring it back ("guardian" is the built-in checks)
recap <workspace> on|off
                     the optional end-of-session recap: what changed, what was not verified, what is at risk
statusline [install|remove]
                     the status line segment (🦆 N notes, ⏰ M reminders due); install wraps your current status line
ui [...]             the same as `tanka ui`: the page, where the codepanion adds Notes, Sessions and Lenses
signals              the signal catalog lenses can wake on
events               for the automation daemon: one JSON signal per line
post-install <ws> <scope>
                     run by `tanka install codepanion <workspace>`
"""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codepanion as c  # noqa: E402

TAP = Path(__file__).resolve().parent / "tap.py"
MARKER = "modules/codepanion/tap.py"
HOOKS = ("SessionStart", "UserPromptSubmit", "PostToolUse", "PostToolUseFailure", "Stop", "SessionEnd")
SETTINGS = c.CLAUDE_HOME / "settings.json"
SEGMENT = f"{sys.executable} {shlex.quote(str(Path(__file__).resolve()))} statusline"

STARTER = {
    "lenses": [],
    "proactivity": 1,
    "notify": False,
    "quiet_hours": "",
    "budget": dict(c.DEFAULT_BUDGET),
    "thresholds": dict(c.DEFAULT_THRESHOLDS),
}


def resolve_ws(arg: str) -> Path:
    ws = (c.kit.WORKSPACES / arg) if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", arg) else Path(arg)
    ws = ws.resolve()
    if not (ws / ".tanka" / "policy.json").is_file():
        raise c.ToolError(f"'{arg}' is not a Tanka workspace. Create it with: tanka start {arg}")
    return ws


def post_install(ws: Path, scope: str, quiet: bool = False) -> int:
    if not c.config_file(ws).is_file():
        c.write_json(c.config_file(ws), STARTER)
        print(f"+ Created {c.config_file(ws)} (no lenses yet).")
    c.lenses_dir(ws).mkdir(parents=True, exist_ok=True)
    c.HOME.mkdir(parents=True, exist_ok=True)
    if not c.watch_file().is_file():
        c.write_json(c.watch_file(), {"projects": {}})
    if quiet or os.environ.get("TANKA_CODEPANION_SETUP"):  # `setup` does the next steps itself
        return 0
    print("  Next (or all at once: tanka codepanion setup <workspace> <project path>):\n"
          f"  1. tanka codepanion watch add <project path> {scope}\n"
          "  2. tanka codepanion tap install            (hooks in your normal Claude Code)\n"
          f"  3. tanka dev {scope}  → /new-codepanion       (build its lenses; it backtests them)\n"
          f"  4. tanka trigger add {scope} watch --on codepanion:* --budget 0.15 \"React to the signal with your lenses.\"\n"
          "  5. tanka automation daemon                 (keep it running)")
    return 0


# ---------------------------------------------------------------- the tap in ~/.claude/settings.json

def load_settings() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.is_file() else {}


def save_settings(data: dict) -> None:
    if SETTINGS.is_file():
        backup = SETTINGS.with_name(f"settings.json.codepanion-backup-{time.strftime('%Y%m%d-%H%M%S')}")
        backup.write_text(SETTINGS.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  Backup: {backup}")
    c.write_json(SETTINGS, data)


def ours(entry: dict) -> bool:
    return any(MARKER in str(h.get("command", "")) for h in entry.get("hooks", []) if isinstance(h, dict))


def tap(action: str) -> int:
    data = load_settings()
    hooks = data.setdefault("hooks", {})
    present = [ev for ev in HOOKS if any(ours(e) for e in hooks.get(ev, []))]
    if action == "status":
        print(f"Tap in {SETTINGS}: {', '.join(present) or 'not installed'}")
        print(f"Watched projects: {', '.join(f'{p} → {w}' for p, w in c.watch().items()) or 'none'}")
        return 0
    if action == "install":
        if len(present) == len(HOOKS):
            print(f"= The tap is already installed in {SETTINGS}.")
            return 0
        cmd = f"{shlex.quote(sys.executable)} {shlex.quote(str(TAP))}"
        for ev in HOOKS:
            if ev in present:
                continue
            entry = {"hooks": [{"type": "command", "command": cmd, "timeout": 5}]}
            if ev in ("PostToolUse", "PostToolUseFailure"):
                entry["matcher"] = "*"
            hooks.setdefault(ev, []).append(entry)
        save_settings(data)
        print(f"+ Tap installed in {SETTINGS} ({len(HOOKS)} hooks, observe-only). It records nothing until a project "
              "is watched: tanka codepanion watch add <path> <workspace>. New Claude Code sessions pick it up.")
        return 0
    if action == "remove":
        if not present:
            print("= The tap is not installed.")
            return 0
        for ev in list(hooks):
            hooks[ev] = [e for e in hooks[ev] if not ours(e)]
            if not hooks[ev]:
                del hooks[ev]
        save_settings(data)
        print(f"- Tap removed from {SETTINGS}. The feed in {c.feed_dir()} is kept; delete it by hand if you want.")
        return 0
    print("Usage: tanka codepanion tap install|remove|status", file=sys.stderr)
    return 1


# ---------------------------------------------------------------- status line

def segment() -> str:
    """🦆 N for new notes, ⏰ M for reminders due; empty when there is neither."""
    n, d = c.unseen(), c.desk_module().due_count()
    return " ".join(x for x in (f"🦆 {n}" if n else "", f"⏰ {d}" if d else "") if x)


def statusline(argv: list[str]) -> int:
    action = argv[0] if argv else ""
    if action == "":
        seg = segment()
        if seg:
            print(seg)
        return 0
    if action == "--then" and len(argv) == 2:
        data = sys.stdin.read()
        p = subprocess.run(argv[1], shell=True, input=data, capture_output=True, text=True, timeout=10)
        seg, out = segment(), p.stdout.rstrip("\n")
        print(f"{out} {seg}" if seg and out else (out or seg))
        return 0
    data = load_settings()
    cur = data.get("statusLine") or {}
    wrapped = SEGMENT in str(cur.get("command", ""))
    if action == "install":
        if wrapped:
            print("= Already in your status line.")
            return 0
        old = cur.get("command")
        new = f"{SEGMENT} --then {shlex.quote(old)}" if old else SEGMENT
        data["statusLine"] = {**cur, "type": "command", "command": new}
        save_settings(data)
        print("+ The codepanion's segment is now in your status line" + (", after your own." if old else "."))
        return 0
    if action == "remove":
        if not wrapped:
            print("= Not in your status line.")
            return 0
        cmd = cur["command"]
        prefix = f"{SEGMENT} --then "
        if cmd.startswith(prefix):
            data["statusLine"] = {**cur, "command": shlex.split(cmd[len(prefix):])[0]}
        else:
            data.pop("statusLine")
        save_settings(data)
        print("- Removed from your status line.")
        return 0
    print("Usage: tanka codepanion statusline [install|remove]", file=sys.stderr)
    return 1


# ---------------------------------------------------------------- notes and feedback

def notes(argv: list[str]) -> int:
    show_all = "--all" in argv
    names = [a for a in argv if not a.startswith("--")]
    scopes = names or sorted(set(c.watch().values()))
    total = 0
    for scope in scopes:
        items = c.read_notes(scope) if show_all else c.mark_seen(scope)
        for n in items:
            total += 1
            print(f"{n['id']} {c.day_time(n['t'])} {scope} [{n['lens']}] {n['text']}\n       evidence: {n['evidence']}")
    if not total:
        print("No new notes." if not show_all else "No notes yet.")
    return 0


def rate(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("good", "bad"):
        print("Usage: tanka codepanion rate <id> good|bad", file=sys.stderr)
        return 1
    c.rate(argv[0], argv[1])
    print(f"+ {argv[0]} rated {argv[1]}. /new-codepanion reads this when you reshape the codepanion.")
    return 0


# ---------------------------------------------------------------- main

def recap(argv: list[str]) -> int:
    """recap <workspace> on|off: the optional end-of-session recap, a lens the module ships."""
    if len(argv) != 2 or argv[1] not in ("on", "off"):
        print("Usage: tanka codepanion recap <workspace> on|off", file=sys.stderr)
        return 1
    ws = resolve_ws(argv[0])
    cfg = c.read_json(c.config_file(ws), {})
    lenses = list(cfg.get("lenses") or [])
    if argv[1] == "off":
        cfg["lenses"] = [x for x in lenses if x != "recap"]
        c.write_json(c.config_file(ws), cfg)
        print("- The recap is off.")
        return 0
    if "recap" not in lenses and len(lenses) >= c.MAX_LENSES:
        print(f"x {argv[0]} already has {c.MAX_LENSES} lenses ({', '.join(lenses)}): turn one off first.", file=sys.stderr)
        return 1
    dest = c.lenses_dir(ws) / "recap.md"
    if not dest.is_file():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(Path(__file__).resolve().parent / "lenses" / "recap.md", dest)
    cfg["lenses"] = lenses if "recap" in lenses else lenses + ["recap"]
    c.write_json(c.config_file(ws), cfg)
    print(f"+ The recap is on: when a session ends, it says what changed, what was not verified and what is at risk "
          f"(one model run per session, within the budget). Edit {dest} to change how.")
    return 0


# ---------------------------------------------------------------- one-command setup

WATCH_TRIGGER = {"on": "codepanion:*", "budget": "0.15", "task": "React to the signal with your lenses."}


def tap_selftest(project: str) -> str | None:
    """Run the installed tap the way Claude Code would, for the watched project; None when the line landed.
    The synthetic session is deleted afterwards, so nothing reaches the lenses."""
    sid = f"selftest-{os.getpid()}-{int(time.time())}"
    env = dict(os.environ, TANKA_CODEPANION_HOME=str(c.HOME), TANKA_WORKSPACES=str(c.kit.WORKSPACES))
    inp = {"hook_event_name": "Stop", "cwd": project, "session_id": sid}
    try:
        subprocess.run([sys.executable, str(TAP)], input=json.dumps(inp), text=True, capture_output=True,
                       timeout=10, env=env)
    except (OSError, subprocess.SubprocessError) as e:
        return f"the tap did not run: {e}"
    line = c.feed_dir() / f"{c.safe_id(sid)}.jsonl"
    landed = line.is_file()
    line.unlink(missing_ok=True)
    return None if landed else f"the tap ran but wrote nothing to {c.feed_dir()}"


def setup(argv: list[str]) -> int:
    """setup <workspace> <project> [--name N] [--language L] [--no-service]: everything a codepanion
    needs, then a self-test with no model."""
    values, rest, i = {}, [], 0
    while i < len(argv):
        if argv[i] in ("--name", "--language") and i + 1 < len(argv):
            values[argv[i][2:]] = argv[i + 1]
            i += 2
        else:
            rest.append(argv[i])
            i += 1
    opts = [a for a in rest if a.startswith("--")]
    args = [a for a in rest if not a.startswith("--")]
    if len(args) != 2 or set(opts) - {"--no-service"}:
        print("Usage: tanka codepanion setup <workspace> <project path> [--name NAME] [--language LANG] [--no-service]",
              file=sys.stderr)
        return 1
    if "name" in values and not 1 <= len(values["name"].strip()) <= 40:
        raise c.ToolError("--name is 1-40 characters: what you call it, e.g. Nova")
    if "language" in values and not re.fullmatch(r"[A-Za-z][A-Za-z -]{1,29}", values["language"]):
        raise c.ToolError("--language is a language code or name, e.g. es or Spanish")
    name, project = args
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
        raise c.ToolError(f"'{name}' is not a workspace name: lowercase letters, digits, - and _")
    if not Path(project).expanduser().is_dir():
        raise c.ToolError(f"{project} is not a directory.")
    import tanka_automation as ta
    import tanka_modules as tm
    ws = c.kit.WORKSPACES / name
    # Children (init, the module's post-install) must see the same homes; post-install stays quiet about next steps.
    child_env = {"TANKA_CODEPANION_HOME": str(c.HOME), "TANKA_WORKSPACES": str(c.kit.WORKSPACES),
                 "TANKA_DESK_HOME": str(c.desk_module().HOME), "TANKA_PAGE_HOME": str(c.kit.PAGE_HOME)}
    sys.stdout.flush()
    if not (ws / ".tanka" / "policy.json").is_file():
        p = subprocess.run([str(c.REPO / "bin" / "tanka"), "init", name], text=True, capture_output=True,
                           env=dict(os.environ, **child_env))
        if p.returncode != 0:
            raise c.ToolError(f"could not create the workspace: {p.stderr.strip() or p.stdout.strip()}")
        print(f"+ Workspace created at {ws}")
    ws = resolve_ws(name)
    if values:
        persona_file = ws / ".tanka" / "persona.json"
        persona = c.read_json(persona_file, {})
        if "name" in values:
            persona["name"] = values["name"].strip()
        if "language" in values:
            persona["language"] = values["language"].strip()
            persona["configured"] = True  # the language is the one question a first session would ask
        c.write_json(persona_file, persona)
        print(f"+ Persona: {persona.get('name') or name}" + (f", speaking {persona['language']}" if persona.get("language") else "")
              + f". The rest (tone, how to address you) is in {persona_file}.")
    # What is missing: the codepanion and the modules it needs (an older install may lack the desk).
    plan = [m["name"] for m in tm.to_install("codepanion", ws) if not (ws / ".claude" / "skills" / m["name"]).is_dir()]
    if not plan:
        post_install(ws, name, quiet=True)  # idempotent: fills in only what is missing
        print("= The codepanion module is already installed.")
    else:
        saved = {k: os.environ.get(k) for k in (*child_env, "TANKA_CODEPANION_SETUP")}
        os.environ.update(child_env, TANKA_CODEPANION_SETUP="1")  # tm.install starts post-install with this env
        try:
            for module in (["codepanion"] if "codepanion" in plan else plan):
                if tm.install(module, ws, name) != 0:
                    return 1
        finally:
            for k, v in saved.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        if "codepanion" not in plan:
            post_install(ws, name, quiet=True)
    print(f"+ Watching {c.watch_set(str(Path(project).expanduser()), name)} for '{name}'.")
    if tap("install") != 0:
        return 1
    cfg = ta.load(ws)
    if "watch" in cfg["triggers"]:
        print("= The trigger 'watch' already exists.")
    else:
        cfg["triggers"]["watch"] = dict(WATCH_TRIGGER)
        ta.save(ws, cfg)
        print(f"+ Trigger 'watch': on {WATCH_TRIGGER['on']}, up to {WATCH_TRIGGER['budget']} USD a run.")
    if ta.daemon_pid():
        print(f"= The automation daemon runs (pid {ta.daemon_pid()}).")
    elif "--no-service" not in opts and ta.service("install") == 0:
        print("+ The automation daemon is a login service now; it restarts on its own.")
    else:
        print(f"+ Started the automation daemon (pid {ta.start_detached()}); it stops when you log out. "
              "Keep it across logins with: tanka automation service install")

    print("\nSelf-test (no model, nothing sent):")
    failures = []
    problems, _ = c.check(ws)
    failures += problems
    print(f"  {'ok' if not problems else 'x'} configuration" + (f": {'; '.join(problems)}" if problems else ""))
    err = tap_selftest(c.real(str(Path(project).expanduser())))
    failures += [err] if err else []
    print(f"  {'ok' if not err else 'x'} the tap writes the feed" + (f": {err}" if err else ""))
    for _ in range(50):  # as long as ensure() waits: systemd can be slow on a cold start
        if ta.daemon_pid():
            break
        time.sleep(0.1)
    alive = ta.daemon_pid()
    failures += [] if alive else ["the automation daemon is not running"]
    print(f"  {'ok' if alive else 'x'} the automation daemon" + (f" (pid {alive})" if alive else " is not running"))
    who = c.persona_name(ws)
    shown = c.tc.desktop_notify(f"{who} · ready", f"Watching {Path(project).expanduser().name}.")
    print(f"  {'ok' if shown else '!'} desktop notification" + ("" if shown else ": none on this machine (notes still go to the page)"))
    if failures:
        print(f"\nx {len(failures)} check(s) failed; fix them and run setup again (it skips what is done).")
        return 1
    print(f"\n+ {who} is set up. Restart open Claude Code sessions in {project}: the tap reaches new sessions only.\n"
          "  It already guards force pushes, secrets in commits and the commit identity, and turns what a session\n"
          "  leaves open into cards. Optional next steps:")
    steps = ((f"tanka codepanion recap {name} on", "end-of-session recap (one model run per session)"),
             (f"tanka dev {name}  → /new-codepanion", "lenses of your own"),
             (f"tanka ui {name}", "the page: chat, Your day, notes, sessions"))
    width = max(len(cmd) for cmd, _ in steps) + 3
    for cmd, what in steps:
        print(f"    {cmd.ljust(width)}{what}")
    return 0


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(line_buffering=True)
    cmd, rest = (argv[0], argv[1:]) if argv else ("help", [])
    try:
        if cmd == "post-install" and len(rest) == 2:
            return post_install(Path(rest[0]), rest[1])
        if cmd == "events":
            for e in c.events():
                print(json.dumps(e, ensure_ascii=False))
            return 0
        if cmd == "watch":
            if rest[:1] == ["add"] and len(rest) == 3:
                resolve_ws(rest[2])
                print(f"+ Watching {c.watch_set(rest[1], rest[2])} for '{rest[2]}'.")
                return 0
            if rest[:1] == ["remove"] and len(rest) == 2:
                print(f"- No longer watching {c.watch_set(rest[1], None)}.")
                return 0
            if rest[:1] in (["list"], []):
                w = c.watch()
                print("\n".join(f"{p} → {ws}" for p, ws in w.items()) or "Nothing watched yet.")
                return 0
            print("Usage: tanka codepanion watch add <path> <workspace> | remove <path> | list", file=sys.stderr)
            return 1
        if cmd == "tap":
            return tap(rest[0] if rest else "status")
        if cmd == "check" and len(rest) == 1:
            ws = resolve_ws(rest[0])
            problems, warnings = c.check(ws)
            for p in problems:
                print(f"x {p}")
            for w in warnings:
                print(f"! {w}")
            lenses = c.load_config(ws)["lenses"]
            print(f"{ws.name}: {'ok' if not problems else f'{len(problems)} problem(s)'}"
                  + (f" ({len(lenses)} lens(es): {', '.join(lenses)})" if lenses and not problems else ""))
            return 0 if not problems else 1
        if cmd == "backtest" and rest:
            ws, days, say = resolve_ws(rest[0]), 14, 0
            opts = rest[1:]
            for i, o in enumerate(opts):
                if o == "--since" and i + 1 < len(opts):
                    m = re.fullmatch(r"(\d+)d?", opts[i + 1])
                    if not m:
                        raise c.ToolError("--since takes days, e.g. 14d")
                    days = int(m.group(1))
                if o == "--say" and i + 1 < len(opts):
                    say = int(opts[i + 1])
            return c.backtest(ws, days, say)
        if cmd == "history" and rest:
            days = 14
            if rest[1:2] == ["--since"] and len(rest) == 3:
                days = int(rest[2].rstrip("d"))
            return c.history(resolve_ws(rest[0]), days)
        if cmd == "notes":
            return notes(rest)
        if cmd == "rate":
            return rate(rest)
        if cmd == "brief":
            print("The daily brief moved to the desk: tanka desk brief <workspace> [HH:MM|off] [--stale DAYS]", file=sys.stderr)
            return 1
        if cmd == "recap":
            return recap(rest)
        if cmd == "setup":
            return setup(rest)
        if cmd == "lens" and len(rest) == 3:
            print(c.lens_action(resolve_ws(rest[0]).name, rest[1], rest[2]))
            return 0
        if cmd == "statusline":
            return statusline(rest)
        if cmd == "ui":
            import tanka_page
            return tanka_page.main(rest)
        if cmd == "signals":
            for name, desc in c.SIGNALS.items():
                print(f"{name:<22} {desc}")
            print(f"Default thresholds: {json.dumps(c.DEFAULT_THRESHOLDS)}")
            return 0
    except c.ToolError as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    print(__doc__.split("\n", 2)[2], file=sys.stderr)
    return 0 if cmd in ("help", "-h", "--help") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
