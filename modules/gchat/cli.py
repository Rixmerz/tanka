#!/usr/bin/env python3
"""`tanka gchat status`: the human side of the Google Chat module.

status       which workspaces use Chat (the Google account accounts.json of the gmail module gives each) and who
             each may write to without asking (bypass in access.json). It does not open the browser.
post-install runs after `tanka install gchat <workspace>`: creates an example access.json when there is none.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gchat  # noqa: E402

STARTER = {"scopes": {"personal": {"bypass": []}}}


def status() -> int:
    print(f"Who each workspace may write to without asking: {gchat.ACCESS} "
          f"({'present' if gchat.ACCESS.is_file() else 'not created; every message waits for Approve'})")
    try:
        accounts = gchat.g.registry() if gchat.g.REGISTRY.is_file() else {}
    except gchat.ToolError as e:
        print(f"x {e}")
        return 1
    if not accounts:
        print(f"No Google account is assigned to a workspace yet ({gchat.g.REGISTRY}); see modules/gmail.")
    for addr, a in sorted(accounts.items()):
        scope = a.get("workspace", "?")
        try:
            bypass = gchat.access(scope).get("bypass") or []
        except gchat.ToolError as e:
            print(f"x {e}")
            return 1
        print(f"  {scope}: {addr}; sends without asking to: {', '.join(map(str, bypass)) or 'nobody (every message waits for Approve)'}")
    return 0


def post_install(ws: Path, scope: str) -> int:
    if not gchat.ACCESS.is_file():
        gchat.HOME.mkdir(parents=True, exist_ok=True)
        gchat.ACCESS.write_text(json.dumps(STARTER, indent=2) + "\n", encoding="utf-8")
        print(f"+ Created {gchat.ACCESS}; every message waits for Approve until you list someone under \"bypass\".")
    print(f"  {scope} uses Chat with the Google account the gmail module gives it, and needs the approvals module "
          f"(tanka install approvals {ws.name}); check with: tanka gchat status")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "post-install" and len(argv) == 3:
        return post_install(Path(argv[1]), argv[2])
    if cmd == "status":
        return status()
    print("Usage: tanka gchat status", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
