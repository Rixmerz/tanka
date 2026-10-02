#!/usr/bin/env python3
"""`tanka companion ui`: a local page to read and rate the companion's notes, follow the watched
sessions, see the lenses and check that everything is running.

The feed holds the user's prompts and commands, so the page is private:

- it listens on 127.0.0.1 only, and refuses a request whose Host header is not that address
  (a DNS-rebinding page cannot reach it);
- the page needs the random token printed in its URL, and every API call sends it in a header
  that another origin cannot set without a preflight this server never answers;
- a strict Content-Security-Policy, and every text the page shows is set as text, never as HTML,
  because notes and prompts may carry markup from what the coding session read.

Its first tab is a chat: what the user writes goes to the workspace's assistant (chat.py), which
keeps the cards with its own tools; the lens notes and the reminders that fired show in the same
stream. Besides the chat, it writes ratings (feedback.jsonl), the seen mark on notes, and ticks,
snoozes or archives cards. Lenses are edited with /new-companion, which checks and backtests them.

Standard library only.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import chat  # noqa: E402
import companion as c  # noqa: E402
import tanka_automation as ta  # noqa: E402

PAGE = Path(__file__).resolve().parent / "ui.html"
MARKER = "modules/companion/tap.py"
DAEMON_FRESH_SECONDS = 60


def ui_file() -> Path:
    return c.HOME / "ui.json"


# ---------------------------------------------------------------- what the page shows

def scopes() -> list[str]:
    return c.card_scopes()


def known_scope(scope: str) -> str:
    if scope not in scopes():
        raise c.ToolError(f"No companion workspace named '{scope}'.")
    return scope


def health() -> dict:
    settings = c.CLAUDE_HOME / "settings.json"
    try:
        hooks = json.loads(settings.read_text(encoding="utf-8")).get("hooks", {}) if settings.is_file() else {}
    except json.JSONDecodeError:
        hooks = {}
    tapped = sorted(ev for ev, entries in hooks.items()
                    if any(MARKER in str(h.get("command", "")) for e in entries if isinstance(e, dict)
                           for h in e.get("hooks", []) if isinstance(h, dict)))
    st = c.state_file()
    age = time.time() - st.stat().st_mtime if st.is_file() else None
    pid = ta.daemon_pid()
    return {"tap": tapped, "tap_ok": len(tapped) == 6, "daemon_pid": pid,
            "daemon_ok": bool(pid) and age is not None and age < DAEMON_FRESH_SECONDS,
            "daemon_seen": round(age) if age is not None else None, "watch": c.watch(), "unseen": c.unseen(), "due": c.due_count()}


def runs_today(ws: Path) -> int:
    log = ws / ".tanka" / "automation.log"
    today = datetime.now().strftime("%Y-%m-%d")
    if not log.is_file():
        return 0
    return sum(1 for ln in log.read_text(encoding="utf-8").splitlines() if ln.startswith(today) and " trigger " in ln and " | exit " in ln)


def session_summary(sid: str, evs: list[dict]) -> dict:
    tools = [e for e in evs if e.get("e") == "tool"]
    start = next((e for e in evs if e.get("e") == "session_start"), evs[0])
    first_prompt = next((e.get("text", "") for e in evs if e.get("e") == "prompt"), "")
    return {"id": sid, "project": evs[0].get("project"), "branch": start.get("branch") or "", "start": evs[0].get("t"),
            "last": evs[-1].get("t"), "prompts": sum(e.get("e") == "prompt" for e in evs), "tools": len(tools),
            "failures": sum(not e.get("ok") for e in tools), "status": c.status_of(evs), "title": first_prompt[:120]}


def scope_state(scope: str) -> dict:
    ws = c.ws_dir(scope)
    out = {"scope": scope, "workspace": str(ws), "installed": c.config_file(ws).is_file(), "projects": c.projects_of(scope)}
    if not out["installed"]:
        return out
    out["pending"] = c.pending(scope)
    try:
        problems, warnings = c.check(ws)
        cfg = c.load_config(ws)
    except c.ToolError as e:
        return dict(out, problems=[str(e)], warnings=[], lenses=[], notes=[], sessions=[])
    verdict = c.verdicts()
    notes = [dict(n, verdict=verdict.get(n["id"])) for n in reversed(c.read_notes(scope))]
    lenses = []
    for name, lens in c.load_lenses(ws).items():
        mine = [n for n in notes if n.get("lens") == name]
        lenses.append({"name": name, "active": name in cfg["lenses"], "meta": lens["meta"], "sections": lens["sections"],
                       "problems": c.lens_problems(name, lens), "notes": len(mine),
                       "good": sum(n["verdict"] == "good" for n in mine), "bad": sum(n["verdict"] == "bad" for n in mine)})
    sessions = [session_summary(sid, evs) for sid, evs in c.sessions_of(scope).items()]
    with c.card_store(scope) as cards:
        everything = list(cards)
    out["chat"] = chat.stream(scope, notes, everything)
    out["busy"] = chat.busy(scope)
    out["draft"] = chat.draft(scope)
    out["chat_left"] = chat.MAX_PER_DAY - chat.sent_today(scope)
    return dict(out, problems=problems, warnings=warnings, config=cfg, lenses=lenses, notes=notes, sessions=sessions,
                runs_today=runs_today(ws), max_usd_today=round(runs_today(ws) * 0.15, 2))


def session_detail(scope: str, sid: str) -> dict:
    sid, evs = c.session_of(scope, sid)
    th = c.load_config(c.ws_dir(scope))["thresholds"]
    fires = c.firings(evs, th) + c.idle_firings(evs, th, until=time.time())
    keep = ("t", "e", "text", "tool", "target", "ok", "error", "lines", "branch", "source", "reason")
    return {"summary": session_summary(sid, evs), "events": [{k: e[k] for k in keep if k in e} for e in evs[-400:]],
            "offset": max(0, len(evs) - 400),
            "firings": [{k: f[k] for k in ("signal", "i", "t", "evidence")} for f in fires]}


# ---------------------------------------------------------------- the server

# Cards are created by the assistant from the chat; the page only ticks, snoozes and archives them.
CARD_ACTIONS = {
    "/api/item": lambda scope, b: c.set_item(scope, str(b.get("id", "")), bool(b.get("done"))),
    "/api/card-action": lambda scope, b: c.update_card(scope, str(b.get("id", "")), str(b.get("action", "")), int(b.get("minutes") or 0)),
}


class Handler(BaseHTTPRequestHandler):
    token = ""
    port = 0
    server_version = "companion-ui"

    def log_message(self, *args):  # the terminal stays quiet; nothing about the feed is printed
        return

    def send(self, code: int, body: bytes, ctype: str, nonce: str = "") -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                         "connect-src 'self'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def json(self, code: int, data) -> None:
        self.send(code, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def allowed_host(self) -> bool:
        return self.headers.get("Host", "") in (f"127.0.0.1:{self.port}", f"localhost:{self.port}")

    def has_token(self, given: str | None) -> bool:
        return bool(given) and hmac.compare_digest(given, self.token)

    def do_GET(self):  # noqa: N802 - http.server's naming
        if not self.allowed_host():
            return self.send(403, b"Forbidden", "text/plain")
        url = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        if url.path == "/":
            if not self.has_token(q.get("t")):
                return self.send(403, b"Open the URL that `tanka companion ui` printed.", "text/plain; charset=utf-8")
            nonce = secrets.token_urlsafe(16)
            page = PAGE.read_text(encoding="utf-8").replace("__NONCE__", nonce).replace("__TOKEN__", self.token)
            return self.send(200, page.encode(), "text/html; charset=utf-8", nonce)
        if not self.has_token(self.headers.get("X-Companion-Token")):
            return self.json(403, {"error": "missing token"})
        try:
            if url.path == "/api/state":
                return self.json(200, {"health": health(), "scopes": [scope_state(s) for s in scopes()]})
            if url.path == "/api/session":
                return self.json(200, session_detail(q.get("scope", ""), q.get("id", "")))
        except c.ToolError as e:
            return self.json(400, {"error": str(e)})
        return self.json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self.allowed_host() or not self.has_token(self.headers.get("X-Companion-Token")):
            return self.json(403, {"error": "forbidden"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.json(415, {"error": "send JSON"})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 10_000)) or b"{}")
            if self.path == "/api/rate":
                return self.json(200, {"note": c.rate(str(body.get("id", "")), str(body.get("verdict", "")))})
            if self.path == "/api/seen":
                return self.json(200, {"seen": len(c.mark_seen(str(body.get("scope", ""))))})
            if self.path == "/api/chat":
                scope = known_scope(str(body.get("scope", "")))
                if not c.config_file(c.ws_dir(scope)).is_file():
                    raise c.ToolError(f"{scope} has no companion: tanka install companion {scope}")
                return self.json(200, {"message": chat.send(scope, str(body.get("text", "")))})
            if self.path in CARD_ACTIONS:
                return self.json(200, {"card": CARD_ACTIONS[self.path](known_scope(str(body.get("scope", ""))), body)})
        except (c.ToolError, ValueError) as e:
            return self.json(400, {"error": str(e)})
        return self.json(404, {"error": "not found"})


def make_server(port: int = 0) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"token": secrets.token_urlsafe(24)})
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    handler.port = srv.server_address[1]
    return srv


def url_of(info: dict) -> str:
    return f"http://127.0.0.1:{info['port']}/?t={info['token']}"


def running() -> dict | None:
    try:
        info = json.loads(ui_file().read_text(encoding="utf-8"))
        os.kill(int(info["pid"]), 0)
        return info
    except (FileNotFoundError, ValueError, KeyError, ProcessLookupError, PermissionError):
        return None


def serve(port: int, open_browser: bool) -> int:
    srv = make_server(port)
    info = {"port": srv.server_address[1], "token": srv.RequestHandlerClass.token, "pid": os.getpid()}
    c.HOME.mkdir(parents=True, exist_ok=True)
    fd = os.open(ui_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the token is a key: only the user reads it
    with os.fdopen(fd, "w") as f:
        json.dump(info, f)
    print(f"Companion UI: {url_of(info)}\n(Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url_of(info))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
        if (running() or {}).get("pid") == os.getpid():
            ui_file().unlink(missing_ok=True)
    return 0


def ensure_daemon() -> None:
    """Reminders notify from the automation daemon's poll, so the page leaves one running."""
    pid = ta.daemon_pid()
    if pid:
        print(f"= The automation daemon is running (pid {pid}): reminders notify.")
        return
    try:
        pid = ta.start_detached()
        jobs = sum(len(cfg["routines"]) + len(cfg["triggers"]) for cfg in map(ta.load, ta.workspaces()))
    except ValueError as e:
        print(f"! Reminders will not notify: {e}", file=sys.stderr)
        return
    print(f"+ Started the automation daemon (pid {pid}) so reminders notify. It also runs your {jobs} routine(s) "
          "and trigger(s), each a model run with its own budget. It keeps running after the UI stops; "
          "stop it with: tanka automation stop")


def main(argv: list[str]) -> int:
    if argv[:1] == ["stop"]:
        info = running()
        if not info:
            print("= The UI is not running.")
            return 0
        os.kill(int(info["pid"]), signal.SIGINT)
        print(f"- Stopped the UI (pid {info['pid']}).")
        return 0
    if "--no-daemon" not in argv:
        ensure_daemon()
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else int(os.environ.get("TANKA_COMPANION_UI_PORT", "0"))
    open_browser = "--no-open" not in argv
    info = running()
    if info:
        print(f"Companion UI (already running): {url_of(info)}")
        if open_browser:
            webbrowser.open(url_of(info))
        return 0
    if "--detach" in argv:
        c.HOME.mkdir(parents=True, exist_ok=True)
        log = (c.HOME / "ui.log").open("a", encoding="utf-8")
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--no-open", "--no-daemon", "--port", str(port)],
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        for _ in range(50):
            time.sleep(0.1)
            info = running()
            if info:
                print(f"Companion UI: {url_of(info)}  (stop it with: tanka companion ui stop)")
                if open_browser:
                    webbrowser.open(url_of(info))
                return 0
        print(f"x The UI did not start; see {c.HOME / 'ui.log'}", file=sys.stderr)
        return 1
    return serve(port, open_browser)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
