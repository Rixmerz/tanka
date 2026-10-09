#!/usr/bin/env python3
"""`tanka cauce …`: the human side of the cauce module.

allow <workspace> <directory> [name]
                     let the workspace see and queue in that repository
deny <workspace> <name>
                     take it away
repos <workspace>    the repositories it may use
board <workspace>    cauce's board, as the assistant sees it
events               for the automation daemon: one JSON line per cauce task that ended since the last poll
post-install <ws> <scope>
                     run by `tanka install cauce <workspace>`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import cauce_link as cl  # noqa: E402


def resolve_scope(arg: str) -> str:
    """The scope is the name given: a workspace may be a symlink, and its repositories live under that name."""
    named = bool(cl.kit.SCOPE_RE.fullmatch(arg))
    ws = cl.kit.ws_dir(arg) if named else Path(arg).resolve()
    if not (ws / ".tanka" / "policy.json").is_file():
        raise cl.ToolError(f"'{arg}' is not a Tanka workspace. Create it with: tanka start {arg}")
    return arg if named else ws.name


def post_install(ws: Path, scope: str) -> int:
    found = cl.version()
    print(f"  cauce: {found}" if found else
          "! cauce is not installed: claude plugin install cauce@rixmerz (or set TANKA_CAUCE_BIN)")
    if not cl.repos(scope):
        print(f"  Next: allow a repository with: tanka cauce allow {scope} <directory>")
    return 0


def main(argv: list[str]) -> int:
    cmd, rest = (argv[0], argv[1:]) if argv else ("help", [])
    try:
        if cmd == "post-install" and len(rest) == 2:
            return post_install(Path(rest[0]), rest[1])
        if cmd == "allow" and len(rest) in (2, 3):
            scope = resolve_scope(rest[0])
            name = cl.allow(scope, rest[1], rest[2] if len(rest) == 3 else None)
            print(f"+ {scope} may use {name} ({cl.repos(scope)[name]})")
            return 0
        if cmd == "deny" and len(rest) == 2:
            scope = resolve_scope(rest[0])
            print(f"- {scope} no longer uses {rest[1]}" if cl.deny(scope, rest[1]) else f"= {scope} had no {rest[1]}")
            return 0
        if cmd == "repos" and len(rest) == 1:
            scope = resolve_scope(rest[0])
            mine = cl.repos(scope)
            print("\n".join(f"{n}  {d}" for n, d in sorted(mine.items())) or f"{scope} may use no repository yet")
            return 0
        if cmd == "events" and not rest:
            for e in cl.endings():
                print(json.dumps(e, ensure_ascii=False))
            return 0
        if cmd == "board" and len(rest) == 1:
            print(cl.board_text(resolve_scope(rest[0])))
            return 0
    except cl.ToolError as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    print(__doc__.split("\n", 2)[2], file=sys.stderr)
    return 0 if cmd in ("help", "-h", "--help") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
