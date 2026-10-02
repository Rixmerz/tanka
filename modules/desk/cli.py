#!/usr/bin/env python3
"""`tanka desk …`: the human side of the desk module.

pending <workspace> [topic]
                     what the workspace's cards hold now, as its assistant sees them
brief <workspace> [HH:MM|off] [--stale DAYS]
                     the daily brief in the chat (default 09:00), and how old an open item is to be called stalled (3)
migrate              move cards, chat and brief settings left by an older codepanion (run on its own too)
events               for the automation daemon: fires due reminders and the brief; prints no events
post-install <ws> <scope>
                     run by `tanka install desk <workspace>`
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import desk as d  # noqa: E402


def resolve_ws(arg: str) -> Path:
    ws = d.kit.ws_dir(arg) if d.kit.SCOPE_RE.fullmatch(arg) else Path(arg).resolve()
    if not (ws / ".tanka" / "policy.json").is_file():
        raise d.ToolError(f"'{arg}' is not a Tanka workspace. Create it with: tanka start {arg}")
    return ws


def post_install(ws: Path, scope: str) -> int:
    for line in d.migrate():
        print(f"+ Moved {line}")
    if not d.config_file(ws).is_file():
        d.write_json(d.config_file(ws), {"brief": d.DEFAULT_BRIEF, "stale_days": d.DEFAULT_STALE_DAYS})
        print(f"+ Created {d.config_file(ws)} (daily brief at {d.DEFAULT_BRIEF}).")
    print(f"  Open the page with: tanka ui {scope}   Reminders notify while the automation daemon runs "
          "(tanka automation service install).")
    return 0


def brief(argv: list[str]) -> int:
    """Show or set when the desk says the day's brief in the chat, and when an item counts as stalled."""
    if not argv:
        print("Usage: tanka desk brief <workspace> [HH:MM|off] [--stale DAYS]", file=sys.stderr)
        return 1
    ws, opts = resolve_ws(argv[0]), argv[1:]
    raw = d.read_json(d.config_file(ws), {})
    if "--stale" in opts:
        i = opts.index("--stale")
        if i + 1 >= len(opts) or not opts[i + 1].isdigit() or not 1 <= int(opts[i + 1]) <= 60:
            raise d.ToolError("--stale takes a number of days from 1 to 60")
        raw["stale_days"] = int(opts[i + 1])
        opts = opts[:i] + opts[i + 2:]
    if opts:
        if opts[0] == "off":
            raw["brief"] = ""
        elif d.brief_time(opts[0]):
            raw["brief"] = opts[0]
        else:
            raise d.ToolError(f"'{opts[0]}' is not a time like 09:00, or off")
    if argv[1:]:
        d.write_json(d.config_file(ws), raw)
    cfg = d.load_config(ws)
    print(f"{ws.name}: " + (f"daily brief at {cfg['brief']}" if cfg["brief"] else "no daily brief")
          + f"; an item open {cfg['stale_days']} day(s) is called stalled.")
    return 0


def main(argv: list[str]) -> int:
    cmd, rest = (argv[0], argv[1:]) if argv else ("help", [])
    try:
        if cmd == "post-install" and len(rest) == 2:
            return post_install(Path(rest[0]), rest[1])
        if cmd == "events":
            d.migrate()
            d.fire_reminders()
            d.daily_brief()
            return 0
        if cmd == "migrate":
            done = d.migrate()
            print("\n".join(f"+ Moved {x}" for x in done) or "= Nothing to move.")
            return 0
        if cmd == "pending" and rest:
            d.list_pending(resolve_ws(rest[0]).name, rest[1] if len(rest) > 1 else None)
            return 0
        if cmd == "brief":
            return brief(rest)
    except d.ToolError as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    print(__doc__.split("\n", 2)[2], file=sys.stderr)
    return 0 if cmd in ("help", "-h", "--help") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
