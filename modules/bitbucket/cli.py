#!/usr/bin/env python3
"""`tanka bitbucket status`: the human side of the Bitbucket module.

status       shows whether the workspace, email and token are configured (never the
             token itself) and which repositories each Tanka workspace may read and
             where it may act without the user's Approve click.
post-install runs after `tanka install bitbucket <workspace>`: creates an example
             scopes.json when there is none.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bitbucket as bb  # noqa: E402


def starter(scope: str) -> dict:
    return {"scopes": {scope: {"repos": ["api-orders"], "bypass": {}, "unattended_write": False}}}


def _bypass_text(bypass: dict) -> str:
    if not bypass:
        return "nothing (every action waits for Approve)"
    return "; ".join(f"{r}: {'any pipeline' if p == bb.ALL else ', '.join(sorted(p))}" for r, p in sorted(bypass.items()))


def status() -> int:
    ws = bb.setting("TANKA_BITBUCKET_WORKSPACE", "workspace")
    email = bb.setting("TANKA_BITBUCKET_EMAIL", "email")
    var = bb.token_var()
    wrapper = bb.file_config().get("token_wrapper")
    print(f"Settings file: {bb.CONFIG} ({'present' if bb.CONFIG.is_file() else 'not created; environment variables only'})")
    print(f"Bitbucket workspace: {ws or 'not set'}")
    print(f"Auth: {'Basic, as ' + email + ' (API token)' if email else 'Bearer (access token; no email set)'}")
    if os.environ.get(var):
        print(f"Token ({var}): set in the environment")
    elif wrapper:
        print(f"Token ({var}): injected by the token_wrapper when a tool runs ({wrapper[0]})")
    else:
        print(f"Token ({var}): not set")
    ca = bb.setting("TANKA_BITBUCKET_CA_FILE", "ca_file")
    if ca:
        print(f"CA bundle: {ca}")
    print(f"Scopes: {bb.SCOPES} ({'present' if bb.SCOPES.is_file() else 'not created yet'})")
    if not bb.SCOPES.is_file():
        return 0
    try:
        entries = bb.scopes()
    except bb.ToolError as e:
        print(f"x {e}")
        return 1
    for name in sorted(entries):
        try:
            s = bb.scope_of(name)
            print(f"  {name}: reads {bb.describe(s['repos'])}; acts without asking on {_bypass_text(s['bypass'])}"
                  f"{'; also unattended' if s['unattended_write'] else ''}")
        except bb.ToolError:
            print(f"  {name}: no valid repository")
    return 0


def post_install(ws: Path, scope: str) -> int:
    if not bb.SCOPES.is_file():
        bb.HOME.mkdir(parents=True, exist_ok=True)
        bb.SCOPES.write_text(json.dumps(starter(scope), indent=2) + "\n", encoding="utf-8")
        print(f"+ Created {bb.SCOPES} with an example; put your repositories there.")
    print(f"  The repositories listed under \"{scope}\" are readable by it; running or stopping a pipeline waits for "
          f"your Approve click unless you list it under bypass. Put the Bitbucket workspace, the email and the token "
          f"variable's name in {bb.CONFIG} (or the environment); check with: tanka bitbucket status")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "post-install" and len(argv) == 3:
        return post_install(Path(argv[1]), argv[2])
    if cmd == "status":
        return status()
    print("Usage: tanka bitbucket status", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
