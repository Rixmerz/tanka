#!/usr/bin/env python3
"""`tanka ui [workspace]`: the local page — a chat with each workspace's assistant, its routines'
reports, what the day cost, and whatever the installed modules add (docs/page.md).

The page shows prompts, notes and whatever the workspaces hold, so it is private:

- it listens on 127.0.0.1 only, and refuses a request whose Host header is not that address
  (a DNS-rebinding page cannot reach it);
- the page needs the random token printed in its URL, and every API call sends it in a header
  that another origin cannot set without a preflight this server never answers;
- a strict Content-Security-Policy with one nonce, and every text the page shows is set as text,
  never as HTML, because notes and prompts may carry markup from what a session read.

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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tanka_automation as ta  # noqa: E402
import tanka_chat as chat  # noqa: E402
import tanka_common as tc  # noqa: E402
import tanka_kit as kit  # noqa: E402
from tanka_kit import ToolError  # noqa: E402

PAGE = kit.REPO / "plugin" / "page" / "page.html"
DAEMON_FRESH_SECONDS = 60
MODULES_MARK = "/*__MODULES__*/"


def ui_file() -> Path:
    return kit.PAGE_HOME / "ui.json"


# ---------------------------------------------------------------- what the page shows

def known_scope(scope: str) -> str:
    if scope not in kit.scopes():
        raise ToolError(f"No workspace named '{scope}'.")
    return scope


def health() -> dict:
    pid, hb = ta.daemon_pid(), ta.heartbeat()
    age = hb.get("age")
    rows = []
    for name, mod in chat.hooks().items():
        fn = getattr(mod, "health", None)
        if fn:
            try:
                rows += [dict(r, module=name) for r in fn()]
            except Exception as e:  # noqa: BLE001
                rows.append({"module": name, "label": name, "ok": False, "text": f"health failed: {e}"})
    return {"daemon_pid": pid, "daemon_ok": bool(pid) and age is not None and age < DAEMON_FRESH_SECONDS,
            "daemon_seen": round(age) if age is not None else None, "daemon_stale": bool(pid) and bool(hb.get("stale")),
            "rows": rows}


def scope_state(scope: str) -> dict:
    ws = kit.ws_dir(scope)
    return {"scope": scope, "name": kit.persona_name(ws), "workspace": str(ws),
            "modules": dict(chat.each(scope, "state")), "chat": chat.stream(scope), "busy": chat.busy(scope),
            "draft": chat.draft(scope), "chat_left": chat.MAX_PER_DAY - chat.sent_today(scope),
            "spent_today": tc.spent_today(ws)}


def module_call(kind: str, path: str, scope: str, arg) -> object:
    """GET or POST /api/m/<module>/<name>, for a module installed in that workspace."""
    parts = path.split("/")
    if len(parts) != 5:
        raise ToolError("not found")
    name, action = parts[3], parts[4]
    mod = dict(chat.installed(known_scope(scope))).get(name)
    fn = (getattr(mod, kind, None) or {}).get(action) if mod else None
    if fn is None:
        raise LookupError(path)
    return fn(scope, kit.ws_dir(scope), arg)


def page_html() -> str:
    """The page, with every module's page.js inlined in its one script (so the CSP keeps one nonce)."""
    parts = []
    for name in chat.hooks():
        js = kit.REPO / "modules" / name / "page.js"
        if js.is_file():
            parts.append(f"// ---- module: {name}\ntry {{\n{js.read_text(encoding='utf-8')}\n}} catch (e) {{ "
                         f"console.error('module {name}', e); }}\n")
    return PAGE.read_text(encoding="utf-8").replace(MODULES_MARK, "\n".join(parts))


# ---------------------------------------------------------------- the server

class Handler(BaseHTTPRequestHandler):
    token = ""
    port = 0
    server_version = "tanka-ui"

    def log_message(self, *args):  # the terminal stays quiet; nothing the page holds is printed
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
                return self.send(403, b"Open the URL that `tanka ui` printed.", "text/plain; charset=utf-8")
            nonce = secrets.token_urlsafe(16)
            page = page_html().replace("__NONCE__", nonce).replace("__TOKEN__", self.token)
            return self.send(200, page.encode(), "text/html; charset=utf-8", nonce)
        if not self.has_token(self.headers.get("X-Tanka-Token")):
            return self.json(403, {"error": "missing token"})
        try:
            if url.path == "/api/state":
                return self.json(200, {"health": health(), "scopes": [scope_state(s) for s in kit.scopes()]})
            if url.path.startswith("/api/m/"):
                return self.json(200, module_call("GETS", url.path, q.get("scope", ""), q))
        except (ToolError, ValueError) as e:
            return self.json(400, {"error": str(e)})
        except LookupError:
            pass
        return self.json(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self.allowed_host() or not self.has_token(self.headers.get("X-Tanka-Token")):
            return self.json(403, {"error": "forbidden"})
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            return self.json(415, {"error": "send JSON"})
        try:
            body = json.loads(self.rfile.read(min(int(self.headers.get("Content-Length") or 0), 10_000)) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("send a JSON object")
            if self.path == "/api/chat":
                scope = known_scope(str(body.get("scope", "")))
                return self.json(200, {"message": chat.send(scope, str(body.get("text", "")))})
            if self.path.startswith("/api/m/"):
                return self.json(200, module_call("ACTIONS", self.path, str(body.get("scope", "")), body))
        except (ToolError, ValueError) as e:
            return self.json(400, {"error": str(e)})
        except LookupError:
            pass
        return self.json(404, {"error": "not found"})


def make_server(port: int = 0) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"token": secrets.token_urlsafe(24)})
    srv = ThreadingHTTPServer(("127.0.0.1", port), handler)
    handler.port = srv.server_address[1]
    return srv


def url_of(info: dict, scope: str | None = None) -> str:
    return f"http://127.0.0.1:{info['port']}/?t={info['token']}" + (f"&ws={quote(scope)}" if scope else "")


def running() -> dict | None:
    try:
        info = json.loads(ui_file().read_text(encoding="utf-8"))
        os.kill(int(info["pid"]), 0)
        return info
    except (FileNotFoundError, ValueError, KeyError, ProcessLookupError, PermissionError):
        return None


def serve(port: int, open_browser: bool, scope: str | None) -> int:
    srv = make_server(port)
    chat.hooks()  # load the modules now, so the files they bring are part of what this page runs
    files = ta.loaded_code()
    info = {"port": srv.server_address[1], "token": srv.RequestHandlerClass.token, "pid": os.getpid(),
            "files": files, "code": ta.code_stamp(files)}
    kit.PAGE_HOME.mkdir(parents=True, exist_ok=True)
    fd = os.open(ui_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the token is a key: only the user reads it
    with os.fdopen(fd, "w") as f:
        json.dump(info, f)
    print(f"Tanka UI: {url_of(info, scope)}\n(Ctrl+C to stop)", flush=True)
    if open_browser:
        webbrowser.open(url_of(info, scope))
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
    """Reminders notify from the automation daemon's poll, so the page leaves one running, on today's code."""
    pid = ta.daemon_pid()
    if pid and ta.heartbeat().get("stale") and ta.heartbeat().get("pid") == pid:
        print(f"! The automation daemon (pid {pid}) runs code older than this checkout: restarting it.")
        ta.restart()
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


def stop() -> int:
    info = running()
    if not info:
        print("= The UI is not running.")
        return 0
    os.kill(int(info["pid"]), signal.SIGINT)
    for _ in range(30):
        if not running():
            break
        time.sleep(0.1)
    print(f"- Stopped the UI (pid {info['pid']}).")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["stop"]:
        return stop()
    names = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--port")]
    scope = known_scope(names[0]) if names else None
    if "--no-daemon" not in argv:
        ensure_daemon()
    port = int(argv[argv.index("--port") + 1]) if "--port" in argv else int(os.environ.get("TANKA_UI_PORT", "0"))
    open_browser = "--no-open" not in argv
    info = running()
    if info and ta.code_stamp(info.get("files") or []) > info.get("code", 0) + 1:
        print(f"! The UI (pid {info['pid']}) runs code older than this checkout: restarting it.")
        stop()
        info = None
    if info:
        print(f"Tanka UI (already running): {url_of(info, scope)}")
        if open_browser:
            webbrowser.open(url_of(info, scope))
        return 0
    if "--detach" in argv:
        kit.PAGE_HOME.mkdir(parents=True, exist_ok=True)
        log = (kit.PAGE_HOME / "ui.log").open("a", encoding="utf-8")
        subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--no-open", "--no-daemon", "--port", str(port)],
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
        for _ in range(50):
            time.sleep(0.1)
            info = running()
            if info:
                print(f"Tanka UI: {url_of(info, scope)}  (stop it with: tanka ui stop)")
                if open_browser:
                    webbrowser.open(url_of(info, scope))
                return 0
        print(f"x The UI did not start; see {kit.PAGE_HOME / 'ui.log'}", file=sys.stderr)
        return 1
    return serve(port, open_browser, scope)


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ToolError as e:
        print(f"x {e}", file=sys.stderr)
        sys.exit(1)
