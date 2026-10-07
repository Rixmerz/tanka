#!/usr/bin/env python3
"""`tanka memory …`: the human side of the memory module. Only here can memories be deleted.

list <workspace> [--archived] [--limit N]
                     the most recent memories (archived ones only with --archived)
search <workspace> <words…>
                     full-text search, as the assistant sees it
show <workspace> <id>
                     one memory in full
forget <workspace> <id> [--yes]
                     delete one memory for good (asks first unless --yes)
export <workspace> [--json]
                     every memory, archived included, as text or JSON
stats [workspace]    counts, where the data lives and how search works
post-install <ws> <scope>
                     run by `tanka install memory <workspace>`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import memory as m  # noqa: E402


def scope_of(arg: str) -> str:
    """A workspace name or a path to one; the scope is the folder's name."""
    return m.check_scope(arg if m.SCOPE_RE.fullmatch(arg) else Path(arg).resolve().name)


def flag_value(opts: list[str], name: str, default: int) -> int:
    if name in opts:
        i = opts.index(name)
        if i + 1 < len(opts) and opts[i + 1].isdigit():
            return int(opts[i + 1])
        raise m.ToolError(f"{name} takes a number")
    return default


def post_install(scope: str) -> int:
    m.home().mkdir(parents=True, exist_ok=True)
    print(f"+ Memories of '{scope}' live in {m.db_path(scope)} (outside the workspace).")
    print(f"  Review them with: tanka memory list {scope}")
    return 0


def forget(scope: str, mid: int, yes: bool) -> int:
    r = m.get(scope, mid)
    print(m.line(r, full=True))
    if not yes:
        if not sys.stdin.isatty():
            raise m.ToolError("Not deleted: run it in a terminal to confirm, or add --yes.")
        if input("Delete this memory for good? [y/N] ").strip().lower() not in ("y", "yes"):
            print("= Kept.")
            return 0
    m.forget(scope, mid)
    print(f"- Deleted memory {mid}.")
    return 0


def stats(scopes: list[str]) -> int:
    if not scopes:
        home = m.home()
        scopes = sorted(p.stem for p in home.glob("*.db")) if home.is_dir() else []
        if not scopes:
            print(f"No memories yet in {home}.")
            return 0
    for s in scopes:
        st = m.stats(s)
        kinds = ", ".join(f"{k} {v}" for k, v in sorted(st["kinds"].items())) or "none"
        print(f"{s}: {st['total']} memories ({st['archived']} archived; limit {st['max_rows']}); {kinds}\n"
              f"  file: {st['path']}\n  search: {st['search']}; schema v{st['schema']}")
    return 0


def dispatch(cmd: str, rest: list[str]) -> int | None:
    if cmd == "post-install" and len(rest) == 2:
        return post_install(m.check_scope(rest[1]))
    if cmd == "stats":
        return stats([scope_of(a) for a in rest])
    if not rest:
        return None
    scope, opts = scope_of(rest[0]), rest[1:]
    if cmd == "list":
        rows = m.search(scope, "", limit=flag_value(opts, "--limit", m.MAX_LIMIT), archived="--archived" in opts)
        print("\n".join(m.line(r) for r in rows) or "No memories.")
        return 0
    if cmd == "search" and opts:
        rows = m.search(scope, " ".join(opts), limit=m.MAX_LIMIT)
        print("\n".join(m.line(r) for r in rows) or "Nothing matches.")
        return 0
    ids = [o for o in opts if o.isdigit()]
    if cmd == "show" and ids:
        r = m.get(scope, int(ids[0]))
        print(m.line(r, full=True) + f"\nsource: {r['source'] or '-'}; updated {r['updated_at']}")
        return 0
    if cmd == "forget" and ids:
        return forget(scope, int(ids[0]), "--yes" in opts or "-y" in opts)
    if cmd == "export":
        rows = m.all_rows(scope)
        for r in rows:
            r.pop("norm", None)
        print(json.dumps(rows, indent=2, ensure_ascii=False) if "--json" in opts
              else "\n".join(m.line(r, full=True) for r in rows) or "No memories.")
        return 0
    return None


def main(argv: list[str]) -> int:
    cmd, rest = (argv[0], argv[1:]) if argv else ("help", [])
    try:
        code = dispatch(cmd, rest)
    except m.ToolError as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    if code is not None:
        return code
    print(__doc__.split("\n", 2)[2], file=sys.stderr)
    return 0 if cmd in ("help", "-h", "--help") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
