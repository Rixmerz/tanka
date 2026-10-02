#!/usr/bin/env python3
"""PreToolUse guard for dev on the page (tanka_chat.dev_cmd): what it may touch, decided here.

Dev runs headless with bypassPermissions, because Claude Code asks before any write under `.claude/`
and a headless run cannot answer; this hook takes that decision instead, and denies anything that
is not on the list below. It fails closed: an error denies.

    tanka_dev_guard.py <workspace> <repository>

- read anywhere in the repository or the workspace (Read, Glob, Grep);
- write only the workspace's views, skills, codepanion lenses and settings, and the desk's settings;
- run only `bin/tanka <command>` for the commands that check and build those, with no shell
  operators, redirections or substitutions;
- Skill (the builder's instructions) and TodoWrite; nothing else (no web, no subagents, no MCP).
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys

READ_TOOLS = ("Read", "Glob", "Grep")
WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")
FREE_TOOLS = ("Skill", "TodoWrite")
WRITABLE = (".claude/views/", ".claude/skills/", ".claude/codepanion/")
WRITABLE_FILES = (".claude/codepanion.json", ".claude/desk.json")
COMMANDS = (("boards",), ("tools", "check"), ("tools", "test"), ("tools", "list"), ("codepanion", "check"),
            ("modules",), ("install",), ("desk", "brief"))
UNSAFE = re.compile(r"[`\n]|\$\(|\$\{")


def inside(path: str, root: str) -> bool:
    p = os.path.realpath(os.path.expanduser(path))
    return p == root or p.startswith(root + os.sep)


def check(tool: str, ti: dict, ws: str, repo: str) -> str | None:
    """None when the call may run, else why not (the model reads it)."""
    if tool in FREE_TOOLS:
        return None
    if tool in READ_TOOLS:
        path = ti.get("file_path") or ti.get("path") or repo
        if inside(path, repo) or inside(path, ws):
            return None
        return f"dev reads only the repository and the workspace, not {path}"
    if tool in WRITE_TOOLS:
        path = os.path.realpath(os.path.expanduser(str(ti.get("file_path") or ti.get("notebook_path") or "")))
        rel = path[len(ws) + 1:] if path.startswith(ws + os.sep) else None
        if rel is not None and (rel.startswith(WRITABLE) or rel in WRITABLE_FILES):
            return None
        return ("dev writes only the workspace's .claude/views, .claude/skills, .claude/codepanion and its "
                f"codepanion.json and desk.json, not {path}")
    if tool == "Bash":
        cmd = str(ti.get("command") or "")
        if UNSAFE.search(cmd):
            return "one bin/tanka command per call, with no substitutions or newlines"
        try:
            lex = shlex.shlex(cmd, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            words = list(lex)
        except ValueError:
            return "the command does not parse; quote its arguments"
        if any(w and set(w) <= set("();<>|&") for w in words):
            return "one bin/tanka command per call: no ;, &&, |, redirections or subshells"
        if words[:1] not in (["bin/tanka"], [os.path.join(repo, "bin", "tanka")]):
            return "dev runs only bin/tanka commands, from the repository"
        if not any(tuple(words[1:1 + len(c)]) == c for c in COMMANDS):
            allowed = ", ".join(" ".join(c) for c in COMMANDS)
            return f"dev runs only these bin/tanka commands: {allowed}"
        return None
    return f"dev on the page cannot use {tool}; build that with `tanka dev` in a terminal"


def main() -> None:
    ws, repo = (os.path.realpath(a) for a in sys.argv[1:3])
    inp = json.load(sys.stdin)
    why = check(str(inp.get("tool_name") or ""), inp.get("tool_input") or {}, ws, repo)
    if why:
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                                 "permissionDecisionReason": why}}))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # fail closed: exit 2 blocks the call and shows the reason
        print(f"tanka dev guard failed, so the call is denied: {e}", file=sys.stderr)
        sys.exit(2)
