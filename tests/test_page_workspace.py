"""The page's Workspace tab: the persona's picture and name, the tool budget, the skills and the modules,
installed and removed from the page. Every home is temporary, and the modules are fakes in a temporary
modules/ directory, so nothing here depends on the shipped ones."""
from __future__ import annotations

import base64
import http.client
import json
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_case import WorkspaceCase, kit  # noqa: E402
import tanka_modules as tm  # noqa: E402
import tanka_page as page  # noqa: E402
import tanka_tools as tt  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'

DESCRIPTION = ("Returns a line for the test suite. Use it only in tests of the workspace tab. "
               "It does nothing else, and no other tool in the workspace does this. Returns one line of text.")


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def write_skill(root: Path, name: str, tools: int, scoped: bool = False, folder: str = "") -> Path:
    """A skill with valid tools: what `tanka tools check` accepts. `folder` names its directory when that is
    not the skill's name (a module ships it as skill/)."""
    d = root / (folder or name)
    (d / "tools").mkdir(parents=True)
    names = [f"{name.replace('-', '_')}_tool{i}" for i in range(tools)]
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: A skill used by the test suite only.\n---\n" + " ".join(names) + "\n")
    for n in names:
        (d / "tools" / f"{n}.py").write_text('SCOPE = "__SCOPE__"\n' if scoped else "print(1)\n")
        (d / "tools" / f"{n}.json").write_text(json.dumps({
            "name": n, "effect": "read", "description": DESCRIPTION, "params": {}, "examples": [{}], "run": ["python3", f"{n}.py"]}))
    return d


class WorkspacePageCase(WorkspaceCase):
    """A page server on a free port, and three fake modules: alpha, beta (needs alpha, has commands of its own and
    a post-install that prints) and gamma (only post-install and events)."""

    def setUp(self):
        super().setUp()
        self.patch(tm, "MODULES", self.tmp / "modules")
        self.make_module("alpha", tools=1)
        self.make_module("beta", tools=2, needs=["alpha"], cli=(
            "import sys\n"
            "cmd = sys.argv[1] if len(sys.argv) > 1 else 'help'\n"
            "if cmd == 'post-install':\n    print('beta post-install ran for ' + sys.argv[3])\n"
            "elif cmd == 'link':\n    print('linked')\n"))
        self.make_module("gamma", tools=1, cli=(
            "import sys\n"
            "cmd = sys.argv[1] if len(sys.argv) > 1 else 'help'\n"
            "if cmd == 'post-install':\n    pass\n"
            "elif cmd == 'events':\n    pass\n"))
        self.srv = page.make_server()
        self.port = self.srv.server_address[1]
        self.token = self.srv.RequestHandlerClass.token
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def make_module(self, name, tools=1, needs=(), cli=None):
        d = tm.MODULES / name
        d.mkdir(parents=True)
        (d / "module.json").write_text(json.dumps({"name": name, "description": f"The {name} test module.",
                                                   "requires": [], "needs": list(needs)}))
        (d / "README.md").write_text(f"# {name}\n")
        write_skill(d, name, tools, scoped=True, folder="skill")
        if cli:
            (d / "cli.py").write_text(cli)
        return d

    def call(self, method, path, body=None, raw=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        h = {"Host": f"127.0.0.1:{self.port}", "X-Tanka-Token": self.token}
        if body is not None or raw is not None:
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=raw if raw is not None else (json.dumps(body) if body is not None else None), headers=h)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        try:
            return r.status, json.loads(data)
        except ValueError:
            return r.status, data

    def scope(self, name="duck"):
        return next(s for s in self.call("GET", "/api/state")[1]["scopes"] if s["scope"] == name)

    def report(self):
        status, data = self.call("GET", "/api/workspace?scope=duck")
        self.assertEqual(status, 200, data)
        return data

    def skill(self, name):
        return next(s for s in self.report()["skills"] if s["name"] == name)

    def module(self, name):
        return next(m for m in self.report()["modules"] if m["name"] == name)


class TestAvatar(WorkspacePageCase):
    def upload(self, data: bytes, kind="image/png", scope="duck"):
        return self.call("POST", "/api/persona/avatar", {"scope": scope, "type": kind, "data": b64(data)})

    def test_png_jpeg_and_webp_are_kept_by_what_they_are(self):
        self.assertIsNone(self.scope()["avatar_v"])
        status, data = self.upload(PNG)
        self.assertEqual(status, 200, data)
        self.assertTrue((kit.PAGE_HOME / "avatars" / "duck.png").is_file())
        self.assertEqual(self.scope()["avatar_v"], data["avatar_v"])
        got = self.call("GET", "/api/persona/avatar?scope=duck")[1]["data"]
        self.assertEqual(got, "data:image/png;base64," + b64(PNG))
        # the declared type is ignored: these bytes are a JPEG, and the PNG goes
        self.assertEqual(self.upload(JPEG, kind="image/png")[0], 200)
        self.assertEqual(sorted(p.name for p in (kit.PAGE_HOME / "avatars").iterdir()), ["duck.jpg"])
        self.assertTrue(self.call("GET", "/api/persona/avatar?scope=duck")[1]["data"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(self.upload(WEBP, kind="image/gif")[0], 200)
        self.assertEqual(sorted(p.name for p in (kit.PAGE_HOME / "avatars").iterdir()), ["duck.webp"])
        self.assertFalse(any(self.ws.rglob("*.webp")))  # never inside the workspace

    def test_svg_gif_garbage_and_bad_base64_are_refused(self):
        for data in (SVG, GIF, b"hello, this is not a picture", b""):
            with self.subTest(data=data[:8]):
                status, body = self.upload(data, kind="image/svg+xml")
                self.assertEqual(status, 400)
                self.assertIn("PNG, JPEG or WebP", body["error"])
        self.assertEqual(self.call("POST", "/api/persona/avatar", {"scope": "duck", "data": "%%%not base64%%%"})[0], 400)
        self.assertFalse((kit.PAGE_HOME / "avatars").exists() and any((kit.PAGE_HOME / "avatars").iterdir()))

    def test_a_picture_over_512_kb_is_refused(self):
        status, body = self.upload(PNG + b"\x00" * (page.AVATAR_MAX + 1 - len(PNG)))
        self.assertEqual(status, 400)
        self.assertIn("512 KB", body["error"])
        self.assertEqual(self.upload(PNG + b"\x00" * (page.AVATAR_MAX - len(PNG)))[0], 200)

    def test_a_body_too_large_is_refused_before_it_is_read(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.putrequest("POST", "/api/persona/avatar", skip_host=True)
        for k, v in {"Host": f"127.0.0.1:{self.port}", "X-Tanka-Token": self.token, "Content-Type": "application/json",
                     "Content-Length": str(page.AVATAR_BODY_MAX + 1)}.items():
            conn.putheader(k, v)
        conn.endheaders()  # no body is sent: the answer must not wait for it
        r = conn.getresponse()
        self.assertEqual(r.status, 413)
        conn.close()

    def test_other_routes_keep_their_small_cap(self):
        status, _ = self.call("POST", "/api/persona/name", raw=json.dumps({"scope": "duck", "name": "x" * 20_000}))
        self.assertEqual(status, 400)  # cut at 10 000 bytes: not JSON any more

    def test_remove_and_unknown_workspace(self):
        self.upload(PNG)
        self.assertEqual(self.call("POST", "/api/persona/avatar-remove", {"scope": "duck"})[0], 200)
        self.assertEqual(self.call("GET", "/api/persona/avatar?scope=duck")[1], {"data": None})
        self.assertIsNone(self.scope()["avatar_v"])
        self.assertEqual(self.upload(PNG, scope="nobody")[0], 400)
        self.assertEqual(self.call("GET", "/api/persona/avatar?scope=nobody")[0], 400)
        self.assertEqual(self.call("GET", "/api/persona/avatar?scope=duck", headers={"X-Tanka-Token": "wrong"})[0], 403)


class TestPersonaName(WorkspacePageCase):
    def persona(self):
        return json.loads((self.ws / ".tanka" / "persona.json").read_text())

    def test_the_name_changes_and_every_other_key_stays(self):
        (self.ws / ".tanka" / "persona.json").write_text(json.dumps({"name": "Old", "language": "es", "tone": "warm", "configured": True}))
        status, data = self.call("POST", "/api/persona/name", {"scope": "duck", "name": "  Nova  "})
        self.assertEqual((status, data), (200, {"name": "Nova"}))
        self.assertEqual(self.persona(), {"name": "Nova", "language": "es", "tone": "warm", "configured": True})
        self.assertEqual(self.scope()["name"], "Nova")
        self.assertEqual(self.report()["persona"], {"name": "Nova", "language": "es", "problem": ""})

    def test_a_name_must_be_one_line_of_1_to_40_characters(self):
        for bad in ("", "   ", "x" * 41, "two\nlines"):
            with self.subTest(name=bad):
                self.assertEqual(self.call("POST", "/api/persona/name", {"scope": "duck", "name": bad})[0], 400)
        self.assertFalse((self.ws / ".tanka" / "persona.json").exists())
        self.assertEqual(self.call("POST", "/api/persona/name", {"scope": "duck", "name": "x" * 40})[0], 200)
        self.assertEqual(self.persona(), {"name": "x" * 40})

    def test_a_broken_persona_file_is_not_overwritten(self):
        f = self.ws / ".tanka" / "persona.json"
        f.write_text("[1, 2")
        self.assertEqual(self.call("POST", "/api/persona/name", {"scope": "duck", "name": "Nova"})[0], 400)
        f.write_text("[1, 2]")
        self.assertEqual(self.call("POST", "/api/persona/name", {"scope": "duck", "name": "Nova"})[0], 400)
        self.assertEqual(f.read_text(), "[1, 2]")
        self.assertIn("not a JSON object", self.report()["persona"]["problem"])  # the tab still shows


class TestSettings(WorkspacePageCase):
    """Every parameter of the workspace by hand: the persona, the rules the hooks enforce, the advisor."""

    def save(self, **body):
        return self.call("POST", "/api/workspace/settings", {"scope": "duck", **body})

    def policy(self):
        return json.loads((self.ws / ".tanka" / "policy.json").read_text())

    def test_the_report_carries_every_setting_with_the_defaults(self):
        st = self.report()["settings"]
        self.assertEqual(st["persona"]["name"], "")
        self.assertEqual(st["policy"]["decisions"], {"read": "allow", "draft": "allow", "modify": "ask", "send": "ask",
                                                     "unknown": "ask"})
        self.assertNotIn("destructive", st["policy"]["decisions"])
        self.assertEqual(st["policy"]["loop_guard"]["max_calls_per_turn"], 25)
        self.assertEqual(st["advisor_model"], "sonnet")

    def test_the_persona_is_saved_whole_and_keeps_what_the_page_does_not_show(self):
        (self.ws / ".tanka" / "persona.json").write_text(json.dumps({"name": "Old", "configured": False, "extra": 1}))
        status, data = self.save(persona={"name": "Nova", "language": "español", "timezone": "America/Santiago",
                                          "signature": "Nova\nassistant of the team", "notes": "  no calls before 9  "})
        self.assertEqual(status, 200, data)
        persona = json.loads((self.ws / ".tanka" / "persona.json").read_text())
        self.assertEqual(persona["name"], "Nova")
        self.assertEqual(persona["signature"], "Nova\nassistant of the team")
        self.assertEqual(persona["notes"], "no calls before 9")
        self.assertTrue(persona["configured"])  # a language set by hand answers the first-run question
        self.assertEqual(persona["extra"], 1)
        for bad in ({"timezone": "Mars/Olympus"}, {"name": ""}, {"tone": "two\nlines"}, {"notes": "x" * 2001}):
            with self.subTest(bad=bad):
                self.assertEqual(self.save(persona={"name": "Nova", **bad})[0], 400)
        self.assertEqual(json.loads((self.ws / ".tanka" / "persona.json").read_text())["name"], "Nova")

    def test_the_rules_are_checked_and_destructive_stays_denied(self):
        (self.ws / ".tanka" / "policy.json").write_text(json.dumps({"tool_classes": {"send": ["x"]}}))
        status, data = self.save(policy={
            "decisions": {"modify": "allow", "send": "ask"}, "external_mcp": "policy",
            "send_validation": {"require_subject": True, "max_recipients": 3, "recipient_allowlist": ["@team\\.com$", ""],
                                "internal_domains": ["Team.com"]},
            "loop_guard": {"max_calls_per_turn": 40}, "closing_report": False})
        self.assertEqual(status, 200, data)
        pol = self.policy()
        self.assertEqual(pol["tool_classes"], {"send": ["x"]})  # what the page does not show stays
        self.assertEqual(pol["decisions"], {"modify": "allow", "send": "ask"})
        self.assertEqual(pol["send_validation"]["recipient_allowlist"], ["@team\\.com$"])
        self.assertEqual(pol["send_validation"]["internal_domains"], ["team.com"])
        self.assertEqual(pol["loop_guard"], {"max_calls_per_turn": 40})
        self.assertFalse(pol["objective"]["require_closing_report_when_tools_used"])
        self.assertEqual(self.report()["settings"]["policy"]["decisions"]["modify"], "allow")
        for bad in ({"decisions": {"destructive": "allow"}}, {"decisions": {"send": "maybe"}},
                    {"external_mcp": "all"}, {"loop_guard": {"max_calls_per_turn": 0}},
                    {"loop_guard": {"max_calls_per_turn": "9"}}, {"send_validation": {"recipient_blocklist": ["(["]}},
                    {"send_validation": {"internal_domains": ["not a domain"]}}):
            with self.subTest(bad=bad):
                status, data = self.save(policy=bad)
                self.assertEqual(status, 400, data)
        self.assertEqual(self.policy(), pol)

    def test_the_advisor_model(self):
        self.assertEqual(self.save(advisor_model="opus")[0], 200)
        self.assertEqual(json.loads((self.ws / ".claude" / "settings.json").read_text())["advisorModel"], "opus")
        self.assertEqual(self.save(advisor_model="gpt")[0], 400)

    def test_a_new_workspace_from_the_page(self):
        status, data = self.call("POST", "/api/workspace/create", {"scope": "shop", "name": "Clara"})
        self.assertEqual(status, 200, data)
        self.assertIn("shop", kit.scopes())
        self.assertTrue((self.wsdir / "shop" / ".tanka" / "policy.json").is_file())
        self.assertEqual(self.scope("shop")["name"], "Clara")
        for bad in ("shop", "Bad Name", "", "ui", "-x"):
            with self.subTest(name=bad):
                self.assertEqual(self.call("POST", "/api/workspace/create", {"scope": bad})[0], 400)


class TestReload(WorkspacePageCase):
    """Reload: what the next message loads, proved before it is sent."""

    def reload(self, what, **extra):
        return self.call("POST", "/api/workspace/reload", {"scope": "duck", "what": what, **extra})

    def test_skills_are_scanned_again(self):
        write_skill(tt.skills_dir(self.ws), "library", tools=2)
        status, data = self.reload("skills")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["skills"], [{"name": "library", "tools": ["library_tool0", "library_tool1"]}])
        self.assertEqual((data["tools"], data["problems"]), (2, []))

    def test_the_mcp_servers_are_started_and_asked_for_their_tools(self):
        write_skill(tt.skills_dir(self.ws), "library", tools=1)
        fake = str(Path(__file__).resolve().parent / "fake_mcp_server.py")
        (self.ws / ".tanka" / "mcp.json").write_text(json.dumps({"mcpServers": {
            "mail": {"command": sys.executable, "args": [fake]},
            "gone": {"command": "/no/such/server"},
            "remote": {"type": "http", "url": "https://example.com/mcp"}}}))
        status, data = self.reload("mcp")
        self.assertEqual(status, 200, data)
        self.assertEqual(data["servers"][0], {"name": "tanka (this workspace's tools)", "ok": True, "tools": ["library_tool0"],
                                              "params": {"library_tool0": []}})
        self.assertEqual(data["external"], "blocked by the rules")
        self.assertEqual(data["skipped"], ["gone", "mail", "remote"])
        (self.ws / ".tanka" / "policy.json").write_text(json.dumps({"external_mcp": "policy"}))
        got = {s["name"]: s for s in self.reload("mcp")[1]["servers"]}
        self.assertEqual(got["mail"]["tools"], ["list_messages", "send_message", "trash_message"])
        self.assertEqual(got["mail"]["params"]["send_message"], ["body", "subject", "to"])  # what the model is offered
        self.assertFalse(got["gone"]["ok"])
        self.assertIn("could not start", got["gone"]["error"])
        self.assertIsNone(got["remote"]["ok"])

    def test_a_silent_or_dying_server_is_reported_not_waited_on(self):
        self.patch(page, "MCP_PROBE_SECONDS", 1.0)
        self.assertIn("no answer to initialize", page.probe_mcp("s", {"command": "sleep", "args": ["30"]}, self.ws)["error"])
        self.assertIn("exited (code 1)", page.probe_mcp("f", {"command": "false"}, self.ws)["error"])

    def test_a_new_conversation(self):
        import tanka_chat as chat
        chat.keep_session("duck", "abc")
        self.assertEqual(chat.session_args("duck")[:2], (["--resume", "abc"], "abc"))
        self.assertEqual(self.reload("conversation")[0], 200)
        extra, _, why = chat.session_args("duck")
        self.assertEqual((extra[0], why), ("--session-id", "reset"))
        self.assertEqual(chat.read("duck")[-1]["who"], "notice")
        self.assertEqual(self.reload("conversation", mode="nope")[0], 400)
        self.assertEqual(self.reload("everything")[0], 400)


class TestConversationAndTools(WorkspacePageCase):
    """The day's conversation resumes only while the assistant's skills and tools are the ones it began with."""

    def test_a_changed_tool_starts_a_new_conversation_with_what_was_said(self):
        import tanka_chat as chat
        write_skill(tt.skills_dir(self.ws), "grading", tools=1)
        chat.append("duck", "you", "review the first test against its answer key")
        chat.append("duck", "tanka", "I cannot: the tool has no parameter for an answer key.")
        chat.keep_session("duck", "s1")
        self.assertEqual(chat.session_args("duck")[2], None)  # same tools: it resumes
        manifest = tt.skills_dir(self.ws) / "grading" / "tools" / "grading_tool0.json"
        m = json.loads(manifest.read_text())
        m["params"] = {"key": {"type": "string", "description": "Path of the answer key file to grade against."}}
        manifest.write_text(json.dumps(m))
        self.assertEqual(self.reload("skills")[1]["restarts"], True)
        runs = []
        self.patch(chat, "run_tanka", lambda scope, text, extra: runs.append((text, extra)) or (0, "Done.", ""))
        chat.send("duck", "try again", background=False)
        prompt, extra = runs[0]
        self.assertEqual(extra[0], "--session-id")
        self.assertIn("restarted because your skills or tools changed", prompt)
        self.assertIn("no parameter for an answer key", prompt)  # quoted as context, flagged as possibly stale
        self.assertTrue(prompt.endswith("try again"))
        notices = [m for m in chat.read("duck") if m.get("who") == "notice"]
        self.assertEqual(len(notices), 1)
        self.assertEqual(chat.session_args("duck")[2], None)  # and the new one resumes from now on

    def test_a_session_saved_without_its_tools_restarts_once(self):
        """Its tools are unknown, so it may be answering from old ones: the case that kept a real
        conversation saying a parameter did not exist after it was added."""
        import tanka_chat as chat
        chat.session_file("duck", "tanka").parent.mkdir(parents=True, exist_ok=True)
        chat.session_file("duck", "tanka").write_text(json.dumps({"id": "old", "day": chat.today()}))
        extra, _, why = chat.session_args("duck")
        self.assertEqual((extra[0], why), ("--session-id", "tools"))
        chat.keep_session("duck", "new")
        self.assertEqual(chat.session_args("duck")[:2], (["--resume", "new"], "new"))

    def reload(self, what):
        return self.call("POST", "/api/workspace/reload", {"scope": "duck", "what": what})


class TestAccess(WorkspacePageCase):
    """Folders outside the workspace: approved by the user, from a request in the chat or by hand in Rules."""

    def setUp(self):
        super().setUp()
        import tanka_common as tc
        self.tc = tc
        self.course = self.tmp / "elsewhere" / "course"
        (self.course / "submissions").mkdir(parents=True)

    def policy(self):
        return json.loads((self.ws / ".tanka" / "policy.json").read_text())

    def test_a_request_shows_in_the_chat_and_approving_adds_the_folder(self):
        req = self.tc.request_access(self.ws, self.course / "submissions", "Read", self.course / "submissions" / "a.pdf")
        item = next(m for m in self.scope()["chat"] if m.get("who") == "access")
        self.assertEqual((item["id"], item["state"]), (req["id"], "pending"))
        # the user may approve the folder above the one asked for
        status, data = self.call("POST", "/api/access", {"scope": "duck", "id": req["id"], "decision": "approve", "dir": str(self.course)})
        self.assertEqual((status, data["state"]), (200, "approved"), data)
        self.assertEqual(self.policy()["read_dirs"], [str(self.course.resolve())])
        self.assertEqual(next(m for m in self.scope()["chat"] if m.get("who") == "access")["state"], "approved")
        self.assertEqual(self.call("POST", "/api/access", {"scope": "duck", "id": req["id"], "decision": "deny"})[1]["state"], "approved")

    def test_denying_and_what_is_refused(self):
        req = self.tc.request_access(self.ws, self.course, "Read", self.course / "x")
        self.assertEqual(self.call("POST", "/api/access", {"scope": "duck", "id": req["id"], "decision": "deny"})[1]["state"], "denied")
        self.assertNotIn("read_dirs", self.policy())
        req = self.tc.request_access(self.ws, self.course, "Read", self.course / "x")
        for body in ({"decision": "maybe"}, {"decision": "approve", "dir": str(Path.home())},
                     {"decision": "approve", "dir": str(self.tmp / "nowhere")}, {"id": "nope", "decision": "approve"}):
            with self.subTest(body=body):
                self.assertEqual(self.call("POST", "/api/access", {"scope": "duck", "id": req["id"], **body})[0], 400)

    def test_folders_by_hand_in_the_rules(self):
        status, data = self.call("POST", "/api/workspace/settings", {"scope": "duck", "policy": {
            "read_dirs": [str(self.course), "file://" + str(self.course / "submissions"), str(self.course), ""]}})
        self.assertEqual(status, 200, data)
        self.assertEqual(self.policy()["read_dirs"], [str(self.course.resolve()), str((self.course / "submissions").resolve())])
        self.assertEqual(self.report()["settings"]["policy"]["read_dirs"], self.policy()["read_dirs"])
        for bad in ([str(Path.home())], [str(Path.home() / ".ssh")], ["relative/path"], [str(self.tmp / "nowhere")], ["/"]):
            with self.subTest(bad=bad):
                self.assertEqual(self.call("POST", "/api/workspace/settings", {"scope": "duck", "policy": {"read_dirs": bad}})[0], 400)


class TestManager(WorkspacePageCase):
    def act(self, action, **body):
        return self.call("POST", f"/api/workspace/{action}", dict(body, scope="duck"))

    def test_the_report_lists_skills_by_origin_and_every_module(self):
        write_skill(tt.skills_dir(self.ws), "notes", 2)
        r = self.report()
        self.assertEqual((r["tools_used"], r["tools_max"]), (2, 15))
        self.assertEqual(r["skills"], [{"name": "notes", "enabled": True, "origin": "own", "module": None,
                                        "tools": [{"name": "notes_tool0", "effect": "read"}, {"name": "notes_tool1", "effect": "read"}]}])
        self.assertEqual([m["name"] for m in r["modules"]], ["alpha", "beta", "gamma"])
        beta = self.module("beta")
        self.assertEqual((beta["tools"], beta["installed"], beta["needs"], beta["needed_by"]), (2, False, ["alpha"], []))
        self.assertEqual(beta["setup"], "tanka beta")  # it has a command of its own
        self.assertIsNone(self.module("gamma")["setup"])  # only post-install and events
        self.assertIsNone(self.module("alpha")["setup"])  # no cli.py
        self.assertEqual(self.call("GET", "/api/workspace?scope=nobody")[0], 400)

    def test_install_from_the_page_brings_what_it_needs_and_says_what_happened(self):
        status, data = self.act("install", module="beta")
        self.assertEqual(status, 200, data)
        self.assertIn("beta needs alpha: installing it first", data["message"])
        self.assertIn("beta post-install ran for duck", data["message"])  # the module's own hook, captured
        self.assertEqual(sorted(p.name for p in tt.skills_dir(self.ws).iterdir()), ["alpha", "beta"])
        r = self.report()
        self.assertEqual(r["tools_used"], 3)
        self.assertEqual({s["name"]: (s["origin"], s["module"]) for s in r["skills"]},
                         {"alpha": ("module", "alpha"), "beta": ("module", "beta")})
        self.assertEqual((self.module("alpha")["installed"], self.module("alpha")["needed_by"]), (True, ["beta"]))
        status, data = self.act("install", module="beta")
        self.assertEqual(status, 400)
        self.assertIn("already exists", data["error"])
        self.assertEqual(self.act("install", module="../alpha")[0], 400)

    def test_an_install_made_before_the_marker_still_counts_as_the_module(self):
        tm.copy_skill(tm.load("alpha"), self.ws, "duck")  # no .module.json
        self.assertEqual((self.skill("alpha")["origin"], self.skill("alpha")["module"]), ("module", "alpha"))
        (tt.skills_dir(self.ws) / "alpha" / "tools" / "alpha_tool0.py").unlink()  # not the module's files any more
        self.assertEqual(self.skill("alpha")["origin"], "own")

    def test_remove_takes_the_skill_only_and_refuses_what_another_needs(self):
        self.act("install", module="beta")
        (self.ws / ".claude" / "beta.json").write_text("{}")
        (self.ws / ".claude" / "views").mkdir()
        status, data = self.act("uninstall", module="alpha")
        self.assertEqual(status, 400)
        self.assertIn("beta needs alpha: remove beta first", data["error"])
        self.assertTrue((tt.skills_dir(self.ws) / "alpha").is_dir())
        status, data = self.act("uninstall", module="beta")
        self.assertEqual(status, 200, data)
        self.assertFalse((tt.skills_dir(self.ws) / "beta").exists())
        self.assertTrue((self.ws / ".claude" / "beta.json").is_file())  # its settings stay
        self.assertTrue((self.ws / ".claude" / "views").is_dir())
        self.assertEqual(self.act("uninstall", module="alpha")[0], 200)
        self.assertEqual(self.report()["tools_used"], 0)
        self.assertEqual(self.act("uninstall", module="alpha")[0], 400)  # not installed any more

    def test_remove_refuses_a_skill_of_the_workspace_own(self):
        write_skill(tt.skills_dir(self.ws), "alpha", 0)  # the module's name, not its files
        status, data = self.act("uninstall", module="alpha")
        self.assertEqual(status, 400)
        self.assertIn("not the alpha module's", data["error"])
        self.assertTrue((tt.skills_dir(self.ws) / "alpha").is_dir())

    def test_an_own_skill_turns_off_and_on(self):
        write_skill(tt.skills_dir(self.ws), "notes", 2)
        self.assertEqual(self.act("skill", skill="notes", enabled=False)[0], 200)
        self.assertTrue((self.ws / ".claude" / "skills.off" / "notes" / "SKILL.md").is_file())
        self.assertFalse((tt.skills_dir(self.ws) / "notes").exists())
        r = self.report()
        self.assertEqual((r["tools_used"], r["skills"][0]["enabled"]), (0, False))
        self.assertEqual(self.act("skill", skill="notes", enabled=True)[0], 200)
        self.assertTrue((tt.skills_dir(self.ws) / "notes").is_dir())
        self.assertEqual(self.report()["tools_used"], 2)

    def test_a_module_skill_is_not_toggled_and_names_cannot_escape(self):
        self.act("install", module="alpha")
        status, data = self.act("skill", skill="alpha", enabled=False)
        self.assertEqual(status, 400)
        self.assertIn("comes from a module", data["error"])
        self.assertTrue((tt.skills_dir(self.ws) / "alpha").is_dir())
        for bad in ("../alpha", "a/b", "", "Notes"):
            self.assertEqual(self.act("skill", skill=bad, enabled=False)[0], 400)
        self.assertEqual(self.act("skill", skill="ghost", enabled=True)[0], 400)

    def test_turning_on_refuses_going_past_15_tools(self):
        for i in range(3):
            write_skill(tt.skills_dir(self.ws), f"full{i}", 5)
        write_skill(self.ws / ".claude" / "skills.off", "extra", 1)
        self.assertEqual(self.report()["tools_used"], 15)
        status, data = self.act("skill", skill="extra", enabled=True)
        self.assertEqual(status, 400)
        self.assertIn("15-tool", data["error"])
        self.assertTrue((self.ws / ".claude" / "skills.off" / "extra").is_dir())
        status, data = self.act("install", module="alpha")
        self.assertEqual(status, 400)
        self.assertIn("15-tool", data["error"])


class TestThePage(WorkspacePageCase):
    def test_one_script_no_html_sinks_and_the_workspace_tab(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", f"/?t={self.token}", headers={"Host": f"127.0.0.1:{self.port}"})
        text = conn.getresponse().read().decode()
        conn.close()
        self.assertEqual(text.count("<script"), 1)
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(sink, text.replace("never touch innerHTML", ""))
        self.assertIn('id: "workspace"', text)
        self.assertIn("/api/persona/avatar", text)
        self.assertIn("Its settings and data stay, and its panel shows while it has data.", text)


if __name__ == "__main__":
    unittest.main()
