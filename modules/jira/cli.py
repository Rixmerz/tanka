#!/usr/bin/env python3
"""`tanka jira status`: the human side of the Jira module.

status       shows whether the site, email and token are configured (never the
             token itself) and which projects each workspace may use.
post-install runs after `tanka install jira <workspace>`: creates an example
             scopes.json when there is none.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira  # noqa: E402

STARTER = {"scopes": {"personal": {"projects": ["PROJ"], "write": False}}}


def status() -> int:
    site = jira.setting("TANKA_JIRA_SITE", "site")
    email = jira.setting("TANKA_JIRA_EMAIL", "email")
    var = jira.token_var()
    wrapper = jira.file_config().get("token_wrapper")
    print(f"Settings file: {jira.CONFIG} ({'present' if jira.CONFIG.is_file() else 'not created; environment variables only'})")
    print(f"Site: {site or 'not set'}")
    print(f"Email: {email or 'not set'}")
    if os.environ.get(var):
        print(f"Token ({var}): set in the environment")
    elif wrapper:
        print(f"Token ({var}): injected by the token_wrapper when a tool runs ({wrapper[0]})")
    else:
        print(f"Token ({var}): not set")
    ca = jira.setting("TANKA_JIRA_CA_FILE", "ca_file")
    if ca:
        print(f"CA bundle: {ca}")
    print(f"Scopes: {jira.SCOPES} ({'present' if jira.SCOPES.is_file() else 'not created yet'})")
    if not jira.SCOPES.is_file():
        return 0
    try:
        entries = jira.scopes()
    except jira.ToolError as e:
        print(f"x {e}")
        return 1
    for ws in sorted(entries):
        try:
            s = jira.scope_of(ws)
            print(f"  {ws}: {', '.join(s['projects'])} ({'read and write' if s['write'] else 'read only'})")
        except jira.ToolError:
            print(f"  {ws}: no valid project")
    return 0


def post_install(ws: Path, scope: str) -> int:
    if not jira.SCOPES.is_file():
        jira.HOME.mkdir(parents=True, exist_ok=True)
        jira.SCOPES.write_text(json.dumps(STARTER, indent=2) + "\n", encoding="utf-8")
        print(f"+ Created {jira.SCOPES} with an example; put your workspaces and project keys there.")
    print(f"  The projects listed under \"{scope}\" are usable by it. Put the site, the email and the token "
          f"variable's name in {jira.CONFIG} (or the environment); check with: tanka jira status")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "post-install" and len(argv) == 3:
        return post_install(Path(argv[1]), argv[2])
    if cmd == "status":
        return status()
    print("Usage: tanka jira status", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
