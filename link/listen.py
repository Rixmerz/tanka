#!/usr/bin/env python3
"""tanka-link: one listener per Claude Code session.

SessionStart and Stop start it in the background (asyncRewake). It registers the session in
~/.tanka/shared/link/sessions/<id>.json and waits for a message in ~/.tanka/shared/link/inbox/<id>/. When one
arrives it prints it and exits with code 2: Claude Code then hands it to the session, even an idle one, and
the next Stop starts a new listener. Only one listener runs per session; SessionEnd unregisters it.
Messages are written only by Tanka, as files the user owns; nothing here opens a port."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HOME = Path(os.environ.get("TANKA_LINK_HOME", Path.home() / ".tanka" / "shared" / "link"))
BEAT = 20          # seconds between heartbeats: Tanka shows a session as live while they are fresh
POLL = 1.0


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def branch(cwd: str) -> str:
    try:
        return subprocess.run(["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True,
                              timeout=3).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def claude_process() -> tuple[int, bool]:
    """(pid of the Claude Code process this hook runs under, whether it is a `claude -p` run). (0, False) if not found."""
    pid = os.getppid()
    for _ in range(8):
        try:
            out = subprocess.run(["ps", "-o", "ppid=,args=", "-p", str(pid)], capture_output=True, text=True, timeout=3).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return 0, False
        if not out:
            return 0, False
        ppid, _, args = out.partition(" ")
        words = args.split()
        if words and Path(words[0]).name == "claude":
            return pid, any(w in ("-p", "--print") for w in words[1:])
        try:
            pid = int(ppid)
        except ValueError:
            return 0, False
        if pid <= 1:
            return 0, False
    return 0, False


def frame(msg: dict) -> str:
    who = "the user, from Tanka's page" if msg.get("from") == "user" else f"the user's assistant {msg.get('from')}, with the user's approval"
    return (f"Message for this session, sent by {who} at {time.strftime('%H:%M', time.localtime(msg.get('t', time.time())))}. "
            "It did not come from this terminal. Do it as a request from the user, within this session's usual permissions; "
            "if it asks for something risky or unclear, ask the user first.\n\n" + str(msg.get("text", "")))


def main(argv: list[str]) -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        return 0
    sid = str(event.get("session_id") or "")
    if not sid or "/" in sid or sid.startswith("."):
        return 0
    reg, pidf, inbox = HOME / "sessions" / f"{sid}.json", HOME / "sessions" / f"{sid}.pid", HOME / "inbox" / sid
    if argv[:1] == ["end"]:
        try:
            os.kill(int(pidf.read_text()), signal.SIGTERM)
        except (OSError, ValueError):
            pass
        for f in (reg, pidf):
            f.unlink(missing_ok=True)
        return 0
    owner, headless = claude_process()
    if headless:  # a script or a worker: nobody reads it, so it is not listed
        return 0
    try:
        if alive(int(pidf.read_text())):
            return 0  # this session already has a listener
    except (OSError, ValueError):
        pass
    pidf.parent.mkdir(parents=True, exist_ok=True)
    pidf.write_text(str(os.getpid()))
    cwd = str(event.get("cwd") or os.getcwd())
    info = {"id": sid, "cwd": cwd, "project": Path(cwd).name, "branch": branch(cwd),
            "transcript": event.get("transcript_path", ""), "started": time.time(), "seen": time.time()}
    try:
        old = json.loads(reg.read_text(encoding="utf-8"))
        info["started"] = old.get("started", info["started"])
    except (OSError, ValueError):
        pass
    write_json(reg, info)
    last = time.time()
    while True:
        waiting = sorted(inbox.glob("*.json")) if inbox.is_dir() else []
        if waiting:
            f = waiting[0]
            try:
                msg = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                f.unlink(missing_ok=True)
                continue
            msg["delivered"] = time.time()
            write_json(HOME / "delivered" / sid / f.name, msg)
            f.unlink(missing_ok=True)
            info["seen"] = time.time()
            write_json(reg, info)
            pidf.unlink(missing_ok=True)
            print(frame(msg), file=sys.stderr)
            return 2
        if time.time() - last > BEAT:
            if not reg.exists() or (owner and not alive(owner)):  # unregistered, or Claude Code is gone: stop
                reg.unlink(missing_ok=True)
                pidf.unlink(missing_ok=True)
                return 0
            info["seen"] = last = time.time()
            write_json(reg, info)
        time.sleep(POLL)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
