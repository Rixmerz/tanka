#!/usr/bin/env python3
"""`tanka calendar login|status`: the human side of the calendar module.

The calendar uses the Gmail module's browser session and sign-in, so:

login  is `tanka gmail login`: a normal browser window on that profile where
       the user signs in to one or more Google accounts.
status lists the accounts assigned to workspaces in accounts.json and checks,
       for each, that the session can open its calendar.
post-install runs after `tanka install calendar <workspace>` and explains where
       the account list lives.
"""
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gcal  # noqa: E402

GMAIL_CLI = Path(__file__).resolve().parent.parent / "gmail" / "cli.py"


def login() -> int:
    print("The calendar uses the Google sign-in of the Gmail session; opening `tanka gmail login`.")
    return subprocess.call([sys.executable, str(GMAIL_CLI), "login"])


def status() -> int:
    path = gcal.registry_path()
    print(f"Accounts and workspaces: {path} ({'present' if path.is_file() else 'not created yet'})")
    if not path.is_file():
        return 1
    accounts = gcal.registry()
    if not accounts:
        print("No account is assigned to any workspace.")
        return 1
    worst = 0
    with gcal.session():
        for address, entry in sorted(accounts.items()):
            try:
                gcal.open_as("agenda", address)
                state = "calendar opens"
            except gcal.ToolError as e:
                state, worst = str(e).split(". ")[0], 1
            unattended = ", unattended_write" if entry.get("unattended_write") else ""
            print(f"  {address}  -> workspace {entry.get('workspace')}{unattended}: {state}")
    return worst


def post_install(scope: str) -> int:
    path = gcal.registry_path()
    print(f"  Accounts whose \"workspace\" is \"{scope}\" in {path} are usable by it "
          f"({gcal.REGISTRY} if you create it, otherwise the Gmail module's list).")
    print("  Sign in once with: tanka calendar login (the same sign-in as tanka gmail login)")
    return 0


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(line_buffering=True)
    cmd = argv[0] if argv else "status"
    try:
        if cmd == "post-install" and len(argv) == 3:
            return post_install(argv[2])
        if cmd in ("login", "status"):
            return {"login": login, "status": status}[cmd]()
    except gcal.ToolError as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    print("Usage: tanka calendar login|status", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
