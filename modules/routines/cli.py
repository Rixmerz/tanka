#!/usr/bin/env python3
"""`tanka routines …`: the user's side of the routines module.

list <workspace>          the workspace's routines, triggers and pending proposals
proposals <workspace>     every proposal kept for the workspace, with its full task
approve <workspace> <id> [--yes]
                          show a proposal in full and activate it as a routine
reject <workspace> <id>   refuse a pending proposal
remove <workspace> <name> remove one routine from the workspace's automations.json
history <workspace>       the last runs of the workspace's routines and triggers
post-install <ws> <scope>
                          run by `tanka install routines <workspace>`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import routines as r  # noqa: E402


def resolve_ws(arg: str) -> tuple[str, Path]:
    """(scope, workspace) from a workspace name or a path to one."""
    ws = (r.ta.WORKSPACES / arg).resolve() if r.SCOPE_RE.fullmatch(arg) else Path(arg).resolve()
    if not (ws / ".tanka" / "policy.json").is_file():
        raise r.ToolError(f"'{arg}' is not a Tanka workspace. Create it with: tanka start {arg}")
    return r.check_scope(arg if r.SCOPE_RE.fullmatch(arg) else ws.name), ws


def post_install(scope: str) -> int:
    r.check_scope(scope)
    (r.HOME / "proposals").mkdir(parents=True, exist_ok=True)
    print(f"  Proposals live in {r.proposals_file(scope)}; limits in {r.HOME / 'limits.json'} (optional).")
    print(f"  Nothing runs until you approve a proposal: Routines tab in `tanka ui {scope}`, or "
          f"`tanka routines approve {scope} <id>`.")
    return 0


def show_proposals(scope: str) -> None:
    items, notice = r.read_proposals(scope)
    if notice:
        print(f"! {notice}")
    if not items:
        print("No proposals.")
    for p in items:
        print(f"{p['id']}  {p['status']:<9} {p['name']}  every {p['every']}  {p['budget']} USD  ({r.ago(p['t'])})")
        if p.get("why"):
            print(f"    why: {p['why']}")
        print(f"    task: {p['task']}")


def approve(scope: str, ws: Path, pid: str, yes: bool) -> int:
    items, _ = r.read_proposals(scope)
    p = r.find(items, pid)
    print(f"Proposal {p['id']} ({p['status']}) from the assistant:")
    print(f"  name:     {p['name']}")
    print(f"  schedule: every {p['every']}")
    print(f"  budget:   up to {p['budget']} USD per run")
    if p.get("why"):
        print(f"  why:      {p['why']}")
    print("  task:")
    for line in str(p["task"]).splitlines() or [""]:
        print(f"    {line}")
    if not yes:
        try:
            answer = input("Activate it? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print("= Not activated.")
            return 1
    print(r.approve(scope, ws, pid))
    return 0


def main(argv: list[str]) -> int:
    cmd, rest = (argv[0], argv[1:]) if argv else ("help", [])
    try:
        if cmd == "post-install" and len(rest) == 2:
            return post_install(rest[1])
        if cmd == "list" and rest:
            scope, ws = resolve_ws(rest[0])
            r.list_tool(scope, ws)
            return 0
        if cmd == "proposals" and rest:
            show_proposals(resolve_ws(rest[0])[0])
            return 0
        if cmd == "approve" and len(rest) >= 2:
            scope, ws = resolve_ws(rest[0])
            return approve(scope, ws, rest[1], "--yes" in rest[2:] or "-y" in rest[2:])
        if cmd == "reject" and len(rest) == 2:
            scope, ws = resolve_ws(rest[0])
            print(r.reject(scope, ws, rest[1]))
            return 0
        if cmd == "remove" and len(rest) == 2:
            scope, ws = resolve_ws(rest[0])
            print(r.remove(scope, ws, rest[1]))
            return 0
        if cmd == "history" and rest:
            scope, ws = resolve_ws(rest[0])
            r.history_tool(scope, ws, 30)
            return 0
    except r.ToolError as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    print(__doc__.split("\n", 2)[2], file=sys.stderr)
    return 0 if cmd in ("help", "-h", "--help") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
