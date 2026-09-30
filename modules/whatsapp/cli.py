#!/usr/bin/env python3
"""`tanka whatsapp link|status`: the human side of the WhatsApp module.

link   opens the headless session and, if it is not linked, draws WhatsApp's QR
       code in this terminal (redrawn each time WhatsApp rotates it) until the
       phone scans it.
status says whether the session is linked and to which number.
post-install runs after `tanka install whatsapp <workspace>`: creates an example
       contacts.json when there is none.
"""
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import wa  # noqa: E402

QR = r"""(() => {
  try { if (window.require("WAWebCollections").Chat.getModelsArray().length) return JSON.stringify({linked: true}); } catch (e) {}
  const el = document.querySelector("[data-ref]");
  return JSON.stringify({ref: el ? el.getAttribute("data-ref") : null});
})()"""

ME = r"""(() => { const u = window.require("WAWebUserPrefsMeUser").getMaybeMePnUser();
  return JSON.stringify({me: u ? u.user : null, chats: window.require("WAWebCollections").Chat.getModelsArray().length}); })()"""


def draw(ref: str) -> None:
    if shutil.which("qrencode"):
        subprocess.run(["qrencode", "-t", "ANSIUTF8", "-m", "2", ref], check=False)
    else:
        png = wa.HOME / "qr.png"
        wa.rastro("screenshot", str(png), timeout=30)
        print(f"(install qrencode to see it here) The code is in {png}")


def link() -> int:
    wa.open_session()
    print("Opening WhatsApp Web without a window…")
    shown, deadline = None, time.time() + 240
    while time.time() < deadline:
        state = wa.js(QR, timeout=30)
        if state.get("linked"):
            info = wa.js(ME, timeout=30)
            print(f"\nLinked: +{info['me']} ({info['chats']} chats). You can close this terminal.")
            return 0
        if state.get("ref") and state["ref"] != shown:
            shown = state["ref"]
            print("\033[2J\033[H", end="")
            print("On the phone: WhatsApp > Linked devices > Link a device, then scan:\n")
            draw(shown)
            print("\n(The code changes about every 20 s and is redrawn here.)")
        time.sleep(2)
    print("Not scanned in time. Run it again: tanka whatsapp link", file=sys.stderr)
    return 1


def status() -> int:
    print(f"Contacts and roles: {wa.REGISTRY} ({'present' if wa.REGISTRY.is_file() else 'not created yet'})")
    try:
        wa.ensure_ready()
    except wa.ToolError as e:
        print(f"WhatsApp: {e}")
        return 1
    info = wa.js(ME, timeout=30)
    print(f"WhatsApp: linked to +{info['me']}, {info['chats']} chats, session {wa.SESSION}, no window.")
    return 0


STARTER = {
    "roles": {"client": {"workspace": "personal", "read": True, "reply": True,
                         "instructions": "Shown to the assistant whenever it reads one of these chats."},
              "friend": {"workspace": "personal", "read": False, "reply": False}},
    "contacts": {"+15551234567": {"name": "Example Person", "role": "client"}},
}


def post_install(argv: list[str]) -> int:
    """Called by `tanka install whatsapp <workspace>` after the skill is copied: post-install <workspace> <scope>."""
    ws, scope = Path(argv[0]), argv[1]
    skill = ws / ".claude" / "skills" / "whatsapp"
    if wa.PEOPLE_DIR != "notes/people":
        for f in skill.rglob("*"):
            if f.is_file() and f.suffix in (".md", ".json"):
                f.write_text(f.read_text(encoding="utf-8").replace("notes/people", wa.PEOPLE_DIR), encoding="utf-8")
    if not wa.REGISTRY.is_file():
        wa.HOME.mkdir(parents=True, exist_ok=True)
        wa.REGISTRY.write_text(json.dumps(STARTER, indent=2) + "\n", encoding="utf-8")
        print(f"+ Created {wa.REGISTRY} with an example; replace it with your contacts and roles.")
    print(f"  Roles whose \"workspace\" is \"{scope}\" are visible to it. Link the phone once with: tanka whatsapp link")
    return 0


def main(argv: list[str]) -> int:
    # qrencode writes straight to the terminal; our own lines must not lag behind it in a buffer.
    sys.stdout.reconfigure(line_buffering=True)
    cmd = argv[0] if argv else "status"
    if cmd == "post-install" and len(argv) == 3:
        return post_install(argv[1:])
    if cmd == "events":
        # For the automation daemon: one JSON event per line, then exit.
        for e in wa.drain_events():
            print(json.dumps(e, ensure_ascii=False))
        return 0
    if cmd == "alert" and len(argv) == 3:
        wa.send_alert(argv[1], argv[2])
        return 0
    try:
        return {"link": link, "status": status}[cmd]()
    except KeyError:
        print("Usage: tanka whatsapp link|status", file=sys.stderr)
        return 1
    except wa.ToolError as e:
        print(f"x {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
