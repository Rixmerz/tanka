#!/usr/bin/env python3
"""`tanka boards …`: the human side of the boards module.

check <workspace>    whether its views can become tools and boards
build <workspace>    write one tool per view (boards_record_<view>), after check; run it after any view change
show <workspace> [view] [match]
                     the boards, or a view's rows, as the assistant sees them
example <workspace>  copy the grades example view into the workspace (then: tanka boards build)
post-install <ws> <scope>
                     run by `tanka install boards <workspace>`
"""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import boards as b  # noqa: E402


def resolve_ws(arg: str) -> Path:
    ws = b.kit.ws_dir(arg) if b.kit.SCOPE_RE.fullmatch(arg) else Path(arg).resolve()
    if not (ws / ".tanka" / "policy.json").is_file():
        raise b.ToolError(f"'{arg}' is not a Tanka workspace. Create it with: tanka start {arg}")
    return ws


def build(ws: Path) -> int:
    done = b.build(ws, ws.name)
    for line in done:
        print(f"+ {line}")
    views = b.load_views(ws)
    print(f"{ws.name}: {len(views)} board(s) ({', '.join(views) or 'none'})"
          + ("" if done else ", nothing to change") + ". New sessions and chat messages see the tools; check with: "
          f"tanka tools check {ws.name}")
    return 0


def post_install(ws: Path, scope: str) -> int:
    b.views_dir(ws).mkdir(parents=True, exist_ok=True)
    if b.load_views(ws):
        b.build(ws, scope)
    print(f"  Next: declare a board with /new-view in tanka dev {scope}, or start from the example:\n"
          f"  tanka boards example {scope} && tanka boards build {scope}")
    return 0


def main(argv: list[str]) -> int:
    cmd, rest = (argv[0], argv[1:]) if argv else ("help", [])
    try:
        if cmd == "post-install" and len(rest) == 2:
            return post_install(Path(rest[0]), rest[1])
        if cmd == "check" and len(rest) == 1:
            ws = resolve_ws(rest[0])
            problems, warnings = b.check(ws)
            for p in problems:
                print(f"x {p}")
            for w in warnings:
                print(f"! {w}")
            print(f"{ws.name}: {'ok' if not problems else f'{len(problems)} problem(s)'}")
            return 0 if not problems else 1
        if cmd == "build" and len(rest) == 1:
            return build(resolve_ws(rest[0]))
        if cmd == "show" and rest:
            ws = resolve_ws(rest[0])
            b.rows_tool(ws.name, rest[1] if len(rest) > 1 else None, rest[2] if len(rest) > 2 else None)
            return 0
        if cmd == "example" and len(rest) == 1:
            ws = resolve_ws(rest[0])
            dest = b.views_dir(ws) / "grades.json"
            if dest.exists():
                raise b.ToolError(f"{dest} already exists.")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(b.MODULE / "examples" / "grades.json", dest)
            print(f"+ {dest}. Edit it to your course's scale and states, then: tanka boards build {ws.name}")
            return 0
    except b.ToolError as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    print(__doc__.split("\n", 2)[2], file=sys.stderr)
    return 0 if cmd in ("help", "-h", "--help") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
