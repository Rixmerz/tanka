#!/usr/bin/env python3
"""`tanka bitbucket login|status`: the human side of the Bitbucket module.

login        opens the module's Rastro session in a visible window on Bitbucket's sign-in page; the user signs
             in there (Atlassian account or Google), presses Enter, and the window closes so the
             tools run the same session headless.
status       shows the workspace, whether the session is open and, when it is, signed in (one GET of the signed-in
             user from inside the page; it never starts a browser), and which repositories each Tanka
             workspace may read and where it may act without the user's Approve click.
post-install runs after `tanka install bitbucket <workspace>`: creates an example scopes.json when there is none.
"""
import json
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


def login() -> int:
    """A visible window of the module's session, so the user signs in themselves; the tools never see a password."""
    bb.HOME.mkdir(parents=True, exist_ok=True)
    if bb.running():
        bb.rastro("close", timeout=60)
    bb.rastro("open", bb.SIGN_IN, "--headed", "--allow-write", ",".join(bb.LOGIN_HOSTS), timeout=180)
    print(f"A browser window of session {bb.session_name()} is open on Bitbucket's sign-in page. Sign in there, wait "
          "until your Bitbucket dashboard shows, then come back here and press Enter.")
    try:
        input()
    except EOFError:
        pass
    bb.rastro("close", timeout=60)  # the tools reopen it headless, allowed to write to bitbucket.org only
    return status()


def session_text() -> tuple[str, bool]:
    """A line about the browser session, and whether it is fine: signed in, or simply not open yet."""
    name = bb.session_name()
    try:
        if not bb.running():
            return (f"Session {name}: not open (the tools open it headless when they need it; if you never signed in, "
                    "run: tanka bitbucket login)"), True
        return f"Session {name}: open, signed in as {bb.signed_in_user()}", True
    except bb.ToolError as e:
        return f"Session {name}: {e}", False


def status() -> int:
    ws = bb.setting("TANKA_BITBUCKET_WORKSPACE", "workspace")
    print(f"Settings file: {bb.CONFIG} ({'present' if bb.CONFIG.is_file() else 'not created; environment variables only'})")
    print(f"Bitbucket workspace: {ws or 'not set'}")
    line, ok = session_text()
    print(line)
    print(f"Scopes: {bb.SCOPES} ({'present' if bb.SCOPES.is_file() else 'not created yet'})")
    if not bb.SCOPES.is_file():
        return 0 if ok else 1
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
    return 0 if ok else 1


def post_install(ws: Path, scope: str) -> int:
    if not bb.SCOPES.is_file():
        bb.HOME.mkdir(parents=True, exist_ok=True)
        bb.SCOPES.write_text(json.dumps(starter(scope), indent=2) + "\n", encoding="utf-8")
        print(f"+ Created {bb.SCOPES} with an example; put your repositories there.")
    print(f"  The repositories listed under \"{scope}\" are readable by it; running or stopping a pipeline waits for "
          f"your Approve click unless you list it under bypass. Put the Bitbucket workspace in {bb.CONFIG} (or "
          f"TANKA_BITBUCKET_WORKSPACE), sign in once with: tanka bitbucket login; check with: tanka bitbucket status")
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "status"
    if cmd == "post-install" and len(argv) == 3:
        return post_install(Path(argv[1]), argv[2])
    try:
        if cmd in ("login", "status"):
            return {"login": login, "status": status}[cmd]()
    except bb.ToolError as e:
        print(f"x {e}", file=sys.stderr)
        return 1
    print("Usage: tanka bitbucket login|status", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
