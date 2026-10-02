#!/usr/bin/env python3
"""`tanka companion …`: the human side of the companion module.

watch add <path> <workspace> | remove <path> | list
                     which projects each companion may see (watch.json)
tap install|remove|status
                     the observe-only hooks in your normal Claude Code (~/.claude/settings.json)
check <workspace>    the rules every companion must pass before it runs
history <workspace> [--since 14d]
                     what the signals would have caught in your past sessions (no lenses needed)
backtest <workspace> [--since 14d] [--say N]
                     replay your past sessions through its lenses; nothing is sent
notes [workspace] [--all]
                     what it said; shown notes are marked seen
rate <id> good|bad   tell it whether a note was worth it
brief <workspace> [HH:MM|off] [--stale DAYS]
                     the daily brief in the chat (default 09:00), and how old an open item is to be called stalled (3)
statusline [install|remove]
                     the status line segment (🦆 N notes, ⏰ M reminders due); install wraps your current status line
ui [--port N] [--no-open] [--detach] [--no-daemon] | ui stop
                     a local page: today's cards, notes with 👍/👎, sessions, lenses, health;
                     it starts the automation daemon if none runs, so reminders notify
signals              the signal catalog lenses can wake on
events               for the automation daemon: one JSON signal per line
post-install <ws> <scope>
                     run by `tanka install companion <workspace>`
"""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import companion as c  # noqa: E402

TAP = Path(__file__).resolve().parent / "tap.py"
MARKER = "modules/companion/tap.py"
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
    ws = (c.WORKSPACES / arg) if re.fullmatch(r"[a-z0-9][a-z0-9_-]*", arg) else Path(arg)
    ws = ws.resolve()
    if not (ws / ".tanka" / "policy.json").is_file():
        raise c.ToolError(f"'{arg}' is not a Tanka workspace. Create it with: tanka start {arg}")
    return ws


def post_install(ws: Path, scope: str) -> int:
    if not c.config_file(ws).is_file():
        c.write_json(c.config_file(ws), STARTER)
        print(f"+ Created {c.config_file(ws)} (no lenses yet).")
    c.lenses_dir(ws).mkdir(parents=True, exist_ok=True)
    c.HOME.mkdir(parents=True, exist_ok=True)
    if not c.watch_file().is_file():
        c.write_json(c.watch_file(), {"projects": {}})
    print("  Next:\n"
          f"  1. tanka companion watch add <project path> {scope}\n"
          "  2. tanka companion tap install            (hooks in your normal Claude Code)\n"
          f"  3. tanka dev {scope}  → /new-companion       (build its lenses; it backtests them)\n"
          f"  4. tanka trigger add {scope} watch --on companion:* --budget 0.15 \"React to the signal with your lenses.\"\n"
          "  5. tanka automation daemon                 (keep it running)")
    return 0


# ---------------------------------------------------------------- the tap in ~/.claude/settings.json

def load_settings() -> dict:
    return json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.is_file() else {}


def save_settings(data: dict) -> None:
    if SETTINGS.is_file():
        backup = SETTINGS.with_name(f"settings.json.companion-backup-{time.strftime('%Y%m%d-%H%M%S')}")
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
              "is watched: tanka companion watch add <path> <workspace>. New Claude Code sessions pick it up.")
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
    print("Usage: tanka companion tap install|remove|status", file=sys.stderr)
    return 1


# ---------------------------------------------------------------- status line

def segment() -> str:
    """🦆 N for new notes, ⏰ M for reminders due; empty when there is neither."""
    n, d = c.unseen(), c.due_count()
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
        print("+ The companion's segment is now in your status line" + (", after your own." if old else "."))
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
    print("Usage: tanka companion statusline [install|remove]", file=sys.stderr)
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
        print("Usage: tanka companion rate <id> good|bad", file=sys.stderr)
        return 1
    c.rate(argv[0], argv[1])
    print(f"+ {argv[0]} rated {argv[1]}. /new-companion reads this when you reshape the companion.")
    return 0


def brief(argv: list[str]) -> int:
    """Show or set when the companion says the day's brief in the chat, and when an item counts as stalled."""
    if not argv:
        print("Usage: tanka companion brief <workspace> [HH:MM|off] [--stale DAYS]", file=sys.stderr)
        return 1
    ws, opts = resolve_ws(argv[0]), argv[1:]
    raw = c.read_json(c.config_file(ws), {})
    if "--stale" in opts:
        i = opts.index("--stale")
        if i + 1 >= len(opts) or not opts[i + 1].isdigit() or not 1 <= int(opts[i + 1]) <= 60:
            raise c.ToolError("--stale takes a number of days from 1 to 60")
        raw["stale_days"] = int(opts[i + 1])
        opts = opts[:i] + opts[i + 2:]
    if opts:
        if opts[0] == "off":
            raw["brief"] = ""
        elif c.brief_time(opts[0]):
            raw["brief"] = opts[0]
        else:
            raise c.ToolError(f"'{opts[0]}' is not a time like 09:00, or off")
    if argv[1:]:
        c.write_json(c.config_file(ws), raw)
    cfg = c.load_config(ws)
    print(f"{ws.name}: " + (f"daily brief at {cfg['brief']}" if cfg["brief"] else "no daily brief")
          + f"; an item open {cfg['stale_days']} day(s) is called stalled.")
    return 0


# ---------------------------------------------------------------- main

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
            print("Usage: tanka companion watch add <path> <workspace> | remove <path> | list", file=sys.stderr)
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
            return brief(rest)
        if cmd == "statusline":
            return statusline(rest)
        if cmd == "ui":
            import ui
            return ui.main(rest)
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
