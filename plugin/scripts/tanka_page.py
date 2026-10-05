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

import base64
import contextlib
import hmac
import io
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import threading
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
import tanka_modules as tm  # noqa: E402
from tanka_kit import ToolError  # noqa: E402

PAGE = kit.REPO / "plugin" / "page" / "page.html"
DAEMON_FRESH_SECONDS = 60
MODULES_MARK = "/*__MODULES__*/"
BODY_MAX = 10_000
AVATAR_BODY_MAX = 800_000      # a 512 KB picture in base64, inside JSON
AVATAR_MAX = 512 * 1024
AVATAR_TYPES = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
NAME_MAX = 40
_manage = threading.Lock()  # one install or removal at a time, and its output captured apart


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


# ---------------------------------------------------------------- the Workspace tab: persona and modules

def persona_file(ws: Path) -> Path:
    return ws / ".tanka" / "persona.json"


def read_persona(ws: Path) -> dict:
    """persona.json as a dict ({} when missing); raises ToolError when it is there but not an object."""
    data = kit.read_json(persona_file(ws), {})
    if not isinstance(data, dict):
        raise ToolError(f"{persona_file(ws)} is not a JSON object; fix it by hand.")
    return data


def set_name(scope: str, name) -> str:
    """Change the persona's name in persona.json, keeping every other key."""
    name = str(name or "").strip()
    if not 1 <= len(name) <= NAME_MAX:
        raise ToolError(f"The name must have 1 to {NAME_MAX} characters.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        raise ToolError("The name must be one line of text.")
    ws = kit.ws_dir(known_scope(scope))
    persona = read_persona(ws)
    persona["name"] = name
    kit.write_json(persona_file(ws), persona)
    return name


# ------------------------------------------------------------ settings the user edits by hand
# Everything a workspace is configured with that a person may change from the page: the persona, the rules
# the hooks enforce, and the advisor model. Each value is checked here, whatever the page sent; a rule the
# harness never relaxes (destructive tools are always denied) is not offered and not accepted.
PERSONA_FIELDS = {  # key: (max characters, more than one line allowed)
    "name": (NAME_MAX, False), "user_name": (60, False), "language": (40, False), "tone": (120, False),
    "personality": (600, True), "output_format": (400, True), "signature": (600, True), "timezone": (64, False),
    "notes": (2000, True),
}
DECISIONS = ("allow", "ask", "deny")
DECIDED_CLASSES = ("read", "draft", "modify", "send", "unknown")  # destructive is always deny
LOOP_LIMITS = {"max_identical_calls_per_turn": (1, 10), "max_calls_per_turn": (1, 200),
               "max_same_tool_per_turn": (1, 100), "max_consecutive_failures": (1, 20),
               "max_calls_per_session": (10, 5000)}
SEND_LIMITS = {"min_body_chars": (0, 2000), "max_recipients": (1, 100)}
PATTERN_LISTS = ("recipient_allowlist", "recipient_blocklist")
ADVISOR_MODELS = ("sonnet", "opus")
DOMAIN_RE = re.compile(r"[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+")


def settings_file(ws: Path) -> Path:
    return ws / ".claude" / "settings.json"


def settings_report(ws: Path) -> dict:
    persona = read_persona(ws)
    policy = tc.load_policy(ws)
    sv, lg = policy["send_validation"], policy["loop_guard"]
    advisor = kit.read_json(settings_file(ws), {})
    return {
        "persona": {k: str(persona.get(k) or "") for k in PERSONA_FIELDS},
        "policy": {
            "decisions": {k: policy["decisions"].get(k, "ask") for k in DECIDED_CLASSES},
            "external_mcp": policy.get("external_mcp", "deny"),
            "send_validation": {"require_subject": bool(sv.get("require_subject")),
                                **{k: int(sv.get(k) or 0) for k in SEND_LIMITS},
                                **{k: list(sv.get(k) or []) for k in (*PATTERN_LISTS, "internal_domains")}},
            "loop_guard": {k: int(lg.get(k) or 0) for k in LOOP_LIMITS},
            "closing_report": bool(policy.get("objective", {}).get("require_closing_report_when_tools_used", True)),
        },
        "advisor_model": str(advisor.get("advisorModel") or "sonnet") if isinstance(advisor, dict) else "sonnet",
        "limits": {"loop_guard": LOOP_LIMITS, "send_validation": SEND_LIMITS, "persona": {k: v[0] for k, v in PERSONA_FIELDS.items()}},
    }


def _text(key: str, value) -> str:
    limit, multiline = PERSONA_FIELDS[key]
    text = str(value or "").strip()
    if len(text) > limit:
        raise ToolError(f"{key} must have at most {limit} characters.")
    if any((ord(ch) < 32 and not (multiline and ch in "\n\t")) or ord(ch) == 127 for ch in text):
        raise ToolError(f"{key} must be {'text' if multiline else 'one line of text'}.")
    return text


def _int(where: str, value, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ToolError(f"{where} must be a whole number from {low} to {high}.")
    return value


def _patterns(where: str, value) -> list[str]:
    if not isinstance(value, list) or len(value) > 50:
        raise ToolError(f"{where} must be a list of at most 50 patterns.")
    out = []
    for item in value:
        item = str(item or "").strip()
        if not item:
            continue
        if len(item) > 200:
            raise ToolError(f"{where}: a pattern has more than 200 characters.")
        try:
            re.compile(item)
        except re.error as e:
            raise ToolError(f"{where}: '{item}' is not a valid pattern ({e}).") from e
        out.append(item)
    return out


def save_settings(scope: str, body: dict) -> dict:
    """Write what the page sent into persona.json, policy.json and settings.json, keeping every key the page
    does not show. All of it is checked before anything is written."""
    ws = kit.ws_dir(known_scope(scope))
    persona_in, policy_in = body.get("persona"), body.get("policy")
    persona = read_persona(ws)
    if persona_in is not None:
        if not isinstance(persona_in, dict):
            raise ToolError("persona must be an object.")
        for key in PERSONA_FIELDS:
            if key in persona_in:
                persona[key] = _text(key, persona_in[key])
        if not persona.get("name"):
            raise ToolError("The assistant needs a name.")
        if persona.get("timezone"):
            try:
                import zoneinfo
                zoneinfo.ZoneInfo(persona["timezone"])
            except (zoneinfo.ZoneInfoNotFoundError, ValueError) as e:
                raise ToolError(f"'{persona['timezone']}' is not a time zone (e.g. America/Santiago).") from e
        if persona.get("language"):
            persona["configured"] = True  # the first-run language question has its answer
    raw = kit.read_json(tc.tanka_dir(ws) / tc.POLICY_FILE, {})
    if not isinstance(raw, dict):
        raise ToolError("policy.json is not a JSON object; fix it by hand.")
    if policy_in is not None:
        if not isinstance(policy_in, dict):
            raise ToolError("policy must be an object.")
        decisions = policy_in.get("decisions") or {}
        if "destructive" in decisions and decisions["destructive"] != "deny":
            raise ToolError("Destructive tools are always denied; that rule cannot be changed.")
        for cls in DECIDED_CLASSES:
            if cls in decisions:
                if decisions[cls] not in DECISIONS:
                    raise ToolError(f"The decision for {cls} must be allow, ask or deny.")
                raw.setdefault("decisions", {})[cls] = decisions[cls]
        if "external_mcp" in policy_in:
            if policy_in["external_mcp"] not in ("deny", "policy"):
                raise ToolError("external_mcp must be deny or policy.")
            raw["external_mcp"] = policy_in["external_mcp"]
        sv_in = policy_in.get("send_validation") or {}
        sv = raw.setdefault("send_validation", {})
        if "require_subject" in sv_in:
            sv["require_subject"] = sv_in["require_subject"] is True
        for key, (low, high) in SEND_LIMITS.items():
            if key in sv_in:
                sv[key] = _int(key, sv_in[key], low, high)
        for key in PATTERN_LISTS:
            if key in sv_in:
                sv[key] = _patterns(key, sv_in[key])
        if "internal_domains" in sv_in:
            domains = [str(d or "").strip().lower() for d in sv_in["internal_domains"] or [] if str(d or "").strip()]
            bad = [d for d in domains if not DOMAIN_RE.fullmatch(d)]
            if bad or len(domains) > 50:
                raise ToolError(f"Not a domain: {', '.join(bad[:3])}." if bad else "At most 50 internal domains.")
            sv["internal_domains"] = domains
        lg_in = policy_in.get("loop_guard") or {}
        for key, (low, high) in LOOP_LIMITS.items():
            if key in lg_in:
                raw.setdefault("loop_guard", {})[key] = _int(key, lg_in[key], low, high)
        if "closing_report" in policy_in:
            raw.setdefault("objective", {})["require_closing_report_when_tools_used"] = policy_in["closing_report"] is True
    settings = None
    if "advisor_model" in body:
        if body["advisor_model"] not in ADVISOR_MODELS:
            raise ToolError(f"The advisor must be one of {', '.join(ADVISOR_MODELS)}.")
        settings = kit.read_json(settings_file(ws), {})
        if not isinstance(settings, dict):
            raise ToolError("settings.json is not a JSON object; fix it by hand.")
        settings["advisorModel"] = body["advisor_model"]
    if persona_in is not None:
        kit.write_json(persona_file(ws), persona)
    if policy_in is not None:
        kit.write_json(tc.tanka_dir(ws) / tc.POLICY_FILE, raw)
    if settings is not None:
        kit.write_json(settings_file(ws), settings)
    return {"ok": True, "message": "Saved. The assistant uses it from its next message."}


def create_workspace(body: dict) -> dict:
    """A new workspace, made by `tanka init` exactly as from a terminal, with its assistant's name."""
    scope = str(body.get("scope", "")).strip()
    if not kit.SCOPE_RE.fullmatch(scope) or len(scope) > 40:
        raise ToolError("The workspace name is its folder: lowercase letters, digits, - and _, starting with a "
                        "letter or digit, at most 40 characters.")
    if scope in kit.scopes():
        raise ToolError(f"There is already a workspace named {scope}.")
    p = subprocess.run([str(kit.REPO / "bin" / "tanka"), "init", scope], capture_output=True, text=True,
                       timeout=60, check=False)
    if p.returncode != 0:
        raise ToolError((p.stderr or p.stdout).strip().removeprefix("x ") or "tanka init did not work.")
    if str(body.get("name", "")).strip():
        set_name(scope, body["name"])
    return {"ok": True, "scope": scope, "message": f"Created {scope}."}


# The picture lives with the page, outside every workspace, so the assistant cannot change its own face.
def avatar_files(scope: str) -> list[Path]:
    root = kit.PAGE_HOME / "avatars"
    return [root / f"{kit.safe_id(scope)}.{ext}" for ext in AVATAR_TYPES]


def avatar_file(scope: str) -> Path | None:
    return next((f for f in avatar_files(scope) if f.is_file()), None)


def avatar_version(scope: str) -> int | None:
    f = avatar_file(scope)
    try:
        return f.stat().st_mtime_ns // 1_000_000 if f else None  # milliseconds: exact as a JavaScript number
    except OSError:
        return None


def image_kind(data: bytes) -> str | None:
    """What the bytes are, by their first bytes, whatever the upload said: PNG, JPEG or WebP, else None."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def set_avatar(scope: str, data) -> int | None:
    known_scope(scope)
    text = str(data or "")
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    raw = base64.b64decode(text, validate=True)
    if len(raw) > AVATAR_MAX:
        raise ToolError(f"The picture is larger than {AVATAR_MAX // 1024} KB. Use a smaller one.")
    kind = image_kind(raw)
    if kind is None:
        raise ToolError("Use a PNG, JPEG or WebP picture.")
    dest = kit.PAGE_HOME / "avatars" / f"{kit.safe_id(scope)}.{kind}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_bytes(raw)
    tmp.replace(dest)
    for f in avatar_files(scope):
        if f != dest:
            f.unlink(missing_ok=True)
    return avatar_version(scope)


def remove_avatar(scope: str) -> None:
    known_scope(scope)
    for f in avatar_files(scope):
        f.unlink(missing_ok=True)


def avatar_data(scope: str) -> str | None:
    f = avatar_file(known_scope(scope))
    if f is None:
        return None
    return f"data:{AVATAR_TYPES[f.suffix[1:]]};base64,{base64.b64encode(f.read_bytes()).decode()}"


def workspace_report(scope: str) -> dict:
    ws = kit.ws_dir(known_scope(scope))
    try:
        language, problem = str(read_persona(ws).get("language") or ""), ""
    except ToolError as e:  # the tab still shows; the persona card says what is wrong
        language, problem = "", str(e)
    try:
        settings = settings_report(ws)
    except ToolError as e:
        settings = {"problem": str(e)}
    return dict(tm.report(ws), scope=scope, settings=settings,
                persona={"name": kit.persona_name(ws), "language": language, "problem": problem})


def manage(fn, *args) -> dict:
    """Run tanka_modules.install or uninstall, with what it prints as the answer's message."""
    out = io.StringIO()
    with _manage, contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = fn(*args)
        except (ValueError, OSError) as e:
            print(f"x {e}")
            code = 1
    message = out.getvalue().strip()
    if code != 0:
        raise ToolError(message or "It did not work.")
    return {"ok": True, "message": message}


def workspace_action(action: str, body: dict) -> dict:
    if action == "create":
        return create_workspace(body)
    scope = known_scope(str(body.get("scope", "")))
    if action == "settings":
        return save_settings(scope, body)
    ws = kit.ws_dir(scope)
    if action == "install":
        return manage(tm.install, str(body.get("module", "")), ws, scope)
    if action == "uninstall":
        return manage(tm.uninstall, str(body.get("module", "")), ws, scope)
    if action == "skill":
        try:
            return {"ok": True, "message": tm.set_enabled(ws, str(body.get("skill", "")), body.get("enabled") is True)}
        except OSError as e:
            raise ToolError(f"Could not move the skill: {e}") from e
    raise LookupError(action)


def scope_state(scope: str) -> dict:
    ws = kit.ws_dir(scope)
    return {"scope": scope, "name": kit.persona_name(ws), "workspace": str(ws), "avatar_v": avatar_version(scope),
            "modules": dict(chat.each(scope, "state")), "chat": chat.stream(scope), "busy": chat.busy(scope),
            "busy_mode": chat.busy_mode(scope), "draft": chat.draft(scope),
            "chat_left": chat.MAX_PER_DAY - chat.sent_today(scope),
            "dev": {"left_usd": chat.dev_left(scope), "per_message_usd": chat.DEV_BUDGET_USD, "model": chat.DEV_MODEL},
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
            if url.path == "/api/commands":
                return self.json(200, chat.commands(known_scope(q.get("scope", ""))))
            if url.path == "/api/workspace":
                return self.json(200, workspace_report(q.get("scope", "")))
            if url.path == "/api/persona/avatar":
                return self.json(200, {"data": avatar_data(q.get("scope", ""))})
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
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.json(400, {"error": "bad Content-Length"})
        if self.path == "/api/persona/avatar" and length > AVATAR_BODY_MAX:
            self.close_connection = True  # the body is never read
            return self.json(413, {"error": f"The picture is larger than {AVATAR_MAX // 1024} KB. Use a smaller one."})
        cap = AVATAR_BODY_MAX if self.path == "/api/persona/avatar" else BODY_MAX
        try:
            body = json.loads(self.rfile.read(min(length, cap)) or b"{}")
            if not isinstance(body, dict):
                raise ValueError("send a JSON object")
            if self.path == "/api/persona/avatar":
                return self.json(200, {"avatar_v": set_avatar(str(body.get("scope", "")), body.get("data"))})
            if self.path == "/api/persona/avatar-remove":
                remove_avatar(str(body.get("scope", "")))
                return self.json(200, {"avatar_v": None})
            if self.path == "/api/persona/name":
                return self.json(200, {"name": set_name(str(body.get("scope", "")), body.get("name"))})
            if self.path.startswith("/api/workspace/"):
                return self.json(200, workspace_action(self.path[len("/api/workspace/"):], body))
            if self.path == "/api/chat":
                scope = known_scope(str(body.get("scope", "")))
                return self.json(200, {"message": chat.send(scope, str(body.get("text", "")), mode=str(body.get("mode") or "tanka"))})
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
