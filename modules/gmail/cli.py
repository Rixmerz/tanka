#!/usr/bin/env python3
"""`tanka gmail login|status`: the human side of the Gmail module.

login  opens a normal browser window on the session's own profile so the user
       can sign in to one or more Google accounts (Gmail keeps them as
       /mail/u/0/, /u/1/, ...), then closes it so the tools run it headless
       again. The window is not automated: Google refuses to sign in inside a
       browser that Playwright drives.
status lists the accounts signed in to the session and which workspace may
       read each, according to accounts.json.
post-install runs after `tanka install gmail <workspace>`: creates an example
       accounts.json when there is none.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gmail  # noqa: E402

SIGN_IN = "https://accounts.google.com/AddSession?continue=https://mail.google.com/mail/"
STARTER = {"accounts": {"someone@example.com": {"workspace": "personal"}}}


def login() -> int:
    gmail.HOME.mkdir(parents=True, exist_ok=True)
    if gmail.running():
        gmail.rastro("close", timeout=60)
    profile = gmail.profile_dir()
    profile.parent.mkdir(parents=True, exist_ok=True)
    window = subprocess.Popen([gmail.real_browser(), f"--user-data-dir={profile}", "--no-first-run", "--no-default-browser-check", SIGN_IN])
    print("A normal browser window is open. It is not automated, because Google refuses to sign in inside an automated "
          "one. Sign in to each Google account this computer should read, then come back here and press Enter.")
    try:
        input()
    except EOFError:
        pass
    window.terminate()  # a graceful quit, so the browser saves the session before the tools use it
    try:
        window.wait(timeout=20)
    except subprocess.TimeoutExpired:
        window.kill()
    return status()


def status() -> int:
    print(f"Accounts and workspaces: {gmail.REGISTRY} ({'present' if gmail.REGISTRY.is_file() else 'not created yet'})")
    assigned = gmail.registry() if gmail.REGISTRY.is_file() else {}
    try:
        with gmail.session_for_cli():
            accounts = gmail.signed_in()
    except gmail.ToolError as e:
        print(f"Gmail: {e}")
        return 1
    print(f"{len(accounts)} account(s) signed in to session {gmail.SESSION}:")
    for a in accounts:
        ws = assigned.get(a["address"], {}).get("workspace")
        print(f"  /u/{a['index']}  {a['address']}  -> {('workspace ' + ws) if ws else 'not assigned to any workspace'}")
    return 0


def post_install(ws: Path, scope: str) -> int:
    if not gmail.REGISTRY.is_file():
        gmail.HOME.mkdir(parents=True, exist_ok=True)
        gmail.REGISTRY.write_text(json.dumps(STARTER, indent=2) + "\n", encoding="utf-8")
        print(f"+ Created {gmail.REGISTRY} with an example; put your addresses there.")
    print(f"  Addresses whose \"workspace\" is \"{scope}\" are readable by it. Sign in once with: tanka gmail login")
    return 0


def main(argv: list[str]) -> int:
    sys.stdout.reconfigure(line_buffering=True)
    cmd = argv[0] if argv else "status"
    try:
        if cmd == "post-install" and len(argv) == 3:
            return post_install(Path(argv[1]), argv[2])
        if cmd == "events":
            # For the automation daemon: one JSON event per line, then exit.
            for e in gmail.drain_events():
                print(json.dumps(e, ensure_ascii=False))
            return 0
        if cmd in ("login", "status"):
            return {"login": login, "status": status}[cmd]()
    except gmail.ToolError as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    print("Usage: tanka gmail login|status", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
