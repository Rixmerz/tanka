"""The page (`tanka ui`): its privacy (host, token, CSP, no HTML sinks), the chat with each workspace,
and the modules that plug into it."""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_case import WorkspaceCase, c, d, kit, out_of  # noqa: E402
import tanka_chat as chat  # noqa: E402
import tanka_page as page  # noqa: E402
import tanka_common as tc  # noqa: E402


class PageCase(WorkspaceCase):
    """A page server on a free port; duck has the desk and a codepanion with a note and a prompt that carries markup."""

    def setUp(self):
        super().setUp()
        self.configure()
        self.hook("UserPromptSubmit", prompt="<img src=x onerror=alert(1)> fix the login")
        out_of(c.note, "duck", "stuck", "What does the key look like when it fails?", "14:02 Bash failed 3 times", "s1")
        self.srv = page.make_server()
        self.port = self.srv.server_address[1]
        self.token = self.srv.RequestHandlerClass.token
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def call(self, method, path, body=None, host=None, token=True, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-Tanka-Token"] = self.token
        if body is not None:
            headers["Content-Type"] = ctype
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, data, r

    def scope(self, name="duck"):
        data = json.loads(self.call("GET", "/api/state")[1])
        return next(s for s in data["scopes"] if s["scope"] == name)


class TestPrivacy(PageCase):
    def test_a_foreign_host_is_refused(self):
        status, _, _ = self.call("GET", f"/?t={self.token}", host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)
        status, _, _ = self.call("GET", "/api/state", host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)

    def test_the_token_is_required(self):
        self.assertEqual(self.call("GET", "/")[0], 403)
        self.assertEqual(self.call("GET", "/?t=wrong")[0], 403)
        self.assertEqual(self.call("GET", "/api/state", token=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/m/codepanion/rate", {"scope": "duck", "id": "x", "verdict": "good"}, token=False)[0], 403)

    def test_no_cross_origin_preflight_is_granted(self):
        status, _, r = self.call("OPTIONS", "/api/chat", token=False)
        self.assertNotEqual(status, 200)
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

    def test_the_page_has_a_strict_policy_and_no_html_sinks(self):
        status, body, r = self.call("GET", f"/?t={self.token}", token=False)
        self.assertEqual(status, 200)
        csp = r.getheader("Content-Security-Policy")
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src 'nonce-", csp)
        text = body.decode()
        self.assertNotIn("__NONCE__", text)
        self.assertNotIn("/*__MODULES__*/", text)
        for module in ("desk", "codepanion"):
            self.assertIn(f'Tanka.module("{module}"', text)  # each module's page.js is inside the one script
        self.assertEqual(text.count("<script"), 1)
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(sink, text.replace("never touch innerHTML", ""))


class TestCommands(PageCase):
    def test_the_commands_each_mode_can_type(self):
        skills = self.ws / ".claude" / "skills"
        (skills / "library").mkdir(parents=True)
        (skills / "library" / "SKILL.md").write_text(
            '---\nname: library\ndescription: "Loans and holds\n  of the library."\nargument-hint: "[book]"\n---\n# Library\n')
        (skills / "quiet").mkdir()
        (skills / "quiet" / "SKILL.md").write_text("---\nname: quiet\ndescription: x\nuser-invocable: false\n---\n")
        (skills / "folded").mkdir()
        (skills / "folded" / "SKILL.md").write_text("---\nname: folded\ndescription: >\n  One line\n  and another.\n---\n")
        (skills / "bare").mkdir()
        (skills / "bare" / "SKILL.md").write_text("# no frontmatter\n")
        status, data, _ = self.call("GET", "/api/commands?scope=duck")
        self.assertEqual(status, 200)
        cmds = json.loads(data)
        tanka = {c["name"]: c for c in cmds["tanka"]}
        self.assertEqual(tanka["/tanka:plan"]["hint"], "[task description | close | show]")
        self.assertEqual(tanka["/tanka:plan"]["origin"], "tanka")
        self.assertEqual(tanka["/library"]["origin"], "workspace")
        self.assertEqual(tanka["/library"]["hint"], "[book]")
        self.assertEqual(tanka["/library"]["description"], "Loans and holds of the library.")
        self.assertNotIn("/quiet", tanka)
        self.assertEqual(tanka["/folded"]["description"], "One line and another.")
        self.assertNotIn("/bare", tanka)
        self.assertTrue(all(len(c["description"]) <= chat.COMMAND_DESC_CHARS for c in cmds["tanka"] + cmds["dev"]))
        self.assertIn("/tanka-dev:new-skill", {c["name"] for c in cmds["dev"]})
        self.assertNotIn("/library", {c["name"] for c in cmds["dev"]})
        self.assertEqual(self.call("GET", "/api/commands?scope=nobody")[0], 400)
        self.assertEqual(self.call("GET", "/api/commands?scope=duck", token=False)[0], 403)

    def test_the_page_carries_the_menu(self):
        html = self.call("GET", "/")[1].decode()
        self.assertIn('id="cmds"', html)
        self.assertIn('id="slash"', html)


class TestModules(PageCase):
    def test_state_carries_each_installed_module(self):
        duck = self.scope()
        cp = duck["modules"]["codepanion"]
        self.assertEqual(len(cp["notes"]), 1)
        self.assertEqual(cp["sessions"][0]["id"], "s1")
        self.assertEqual(cp["lenses"][0]["name"], "stuck")
        self.assertEqual(cp["problems"], [])
        self.assertIn("pending", duck["modules"]["desk"])

    def test_a_workspace_without_modules_still_chats(self):
        (self.wsdir / "plain" / ".tanka").mkdir(parents=True)
        (self.wsdir / "plain" / ".tanka" / "policy.json").write_text("{}")
        plain = self.scope("plain")
        self.assertEqual(plain["modules"], {})
        self.assertEqual(plain["chat"], [])

    def test_session_detail_has_the_timeline_and_firings(self):
        for _ in range(3):
            self.fail()
        status, data, _ = self.call("GET", "/api/m/codepanion/session?scope=duck&id=s1")
        self.assertEqual(status, 200)
        got = json.loads(data)
        self.assertEqual(got["events"][0]["e"], "prompt")
        self.assertEqual([f["signal"] for f in got["firings"]], ["stuck"])

    def test_rating_from_the_page_is_recorded(self):
        note_id = c.read_notes("duck")[0]["id"]
        status, _, _ = self.call("POST", "/api/m/codepanion/rate", {"scope": "duck", "id": note_id, "verdict": "bad"})
        self.assertEqual(status, 200)
        self.assertEqual(c.verdicts(), {note_id: "bad"})
        self.assertTrue(c.read_notes("duck")[0]["seen"])
        self.assertEqual(self.call("POST", "/api/m/codepanion/rate", {"scope": "duck", "id": note_id, "verdict": "meh"})[0], 400)
        self.assertEqual(self.call("POST", "/api/m/codepanion/rate", {"scope": "duck", "id": note_id, "verdict": "good"}, ctype="text/plain")[0], 415)

    def test_the_page_ticks_cards_but_does_not_create_them(self):
        item = d.add_card("duck", "check", "app", "Pagar la luz")
        self.assertEqual(self.call("POST", "/api/m/desk/add", {"scope": "duck", "kind": "check", "text": "x"})[0], 404)
        self.assertEqual(self.call("POST", "/api/m/desk/item", {"scope": "duck", "id": item["id"], "done": True})[0], 200)
        self.assertIsNotNone(d.pending("duck")["checks"][0]["items"][0]["done_at"])
        rid = d.add_card("duck", "reminder", "", "Llamar", "23:59")["id"]
        self.assertEqual(self.call("POST", "/api/m/desk/card", {"scope": "duck", "id": rid, "action": "archive"})[0], 200)
        self.assertEqual(d.pending("duck")["reminders"], [])
        self.assertEqual(self.call("POST", "/api/m/desk/item", {"scope": "nobody", "id": item["id"], "done": False})[0], 400)

    def test_a_module_not_installed_in_that_workspace_has_no_routes(self):
        (self.ws / ".claude" / "desk.json").unlink()
        item = d.add_card("duck", "check", "app", "Pagar la luz")
        self.assertEqual(self.call("POST", "/api/m/desk/item", {"scope": "duck", "id": item["id"], "done": True})[0], 404)
        self.assertNotIn("desk", self.scope()["modules"])

    def test_health_has_the_daemon_and_each_module(self):
        h = json.loads(self.call("GET", "/api/state")[1])["health"]
        self.assertIn("daemon_ok", h)
        labels = {r["label"] for r in h["rows"]}
        self.assertIn("Reminders due", labels)
        self.assertIn("Codepanion tap", labels)


class TestFiles(PageCase):
    """Files dropped on the chat: copied into the workspace's files/ folder, never over another, never outside it."""

    def upload(self, name, data, ctype="application/octet-stream", token=True, scope="duck"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": f"127.0.0.1:{self.port}", "Content-Type": ctype, "Content-Length": str(len(data))}
        if token:
            headers["X-Tanka-Token"] = self.token
        from urllib.parse import quote
        conn.request("POST", f"/api/files?scope={scope}&name={quote(name)}", body=data, headers=headers)
        r = conn.getresponse()
        body = r.read()
        conn.close()
        return r.status, json.loads(body or b"{}")

    def test_a_dropped_file_lands_in_files_and_never_over_another(self):
        status, got = self.upload("report.pdf", b"%PDF-1.4 one")
        self.assertEqual((status, got), (200, {"path": "files/report.pdf", "name": "report.pdf", "size": 12}))
        self.assertEqual((self.ws / "files" / "report.pdf").read_bytes(), b"%PDF-1.4 one")
        self.assertEqual(self.upload("report.pdf", b"two")[1]["path"], "files/report (2).pdf")
        self.assertEqual((self.ws / "files" / "report.pdf").read_bytes(), b"%PDF-1.4 one")

    def test_a_name_cannot_leave_the_folder_or_hide(self):
        for name, want in (("../../escape.txt", "escape.txt"), ("..\\..\\win.txt", "win.txt"), (".env", "env"),
                           ("a\nb.txt", "a_b.txt"), ("", "file")):
            with self.subTest(name=name):
                self.assertEqual(self.upload(name, b"x")[1]["name"], want)
        self.assertFalse((self.tmp / "escape.txt").exists())
        self.assertEqual(sorted(p.parent for p in (self.ws / "files").iterdir()), [self.ws / "files"] * 5)

    def test_what_is_refused(self):
        self.assertEqual(self.upload("a.txt", b"x", token=False)[0], 403)
        self.assertEqual(self.upload("a.txt", b"x", ctype="application/json")[0], 415)
        self.assertEqual(self.upload("a.txt", b"x", scope="nobody")[0], 400)
        self.patch(page, "UPLOAD_MAX", 4)
        status, got = self.upload("big.bin", b"12345")
        self.assertEqual(status, 413)
        self.assertFalse((self.ws / "files" / "big.bin").exists())


class TestChat(PageCase):
    """The page's chat, with the model run replaced: what reaches `tanka run`, and what comes back."""

    def setUp(self):
        super().setUp()
        self.runs = []
        self.replies = []

        def fake(scope, text, extra):
            self.runs.append((scope, text, list(extra)))
            return self.replies.pop(0) if self.replies else (0, "Listo: te lo recuerdo a las 18:00.", "")
        self.patch(chat, "run_tanka", fake)
        os.environ["TANKA_CODEPANION_BACKTEST"] = "1"  # no desktop notification from a test
        self.addCleanup(os.environ.pop, "TANKA_CODEPANION_BACKTEST", None)

    def test_a_message_names_the_files_and_the_assistant_is_told_to_read_them(self):
        (self.ws / "files").mkdir()
        (self.ws / "files" / "notes.pdf").write_bytes(b"%PDF")
        status, data, _ = self.call("POST", "/api/chat", {"scope": "duck", "text": "", "files": ["files/notes.pdf"]})
        self.assertEqual(status, 200, data)
        for _ in range(100):
            if not chat.busy("duck"):
                break
            time.sleep(0.05)
        self.assertIn("read them with Read", self.runs[-1][1])
        self.assertIn(str(self.ws / "files" / "notes.pdf"), self.runs[-1][1])  # the full path, never a guessed one
        mine = [m for m in chat.read("duck") if m.get("who") == "you"][-1]
        self.assertEqual((mine["text"], mine["files"]), ("", ["files/notes.pdf"]))
        for bad in (["../.tanka/policy.json"], ["files/missing.pdf"], [".tanka/persona.json"], ["files/x"] * 11):
            with self.subTest(files=bad):
                self.assertEqual(self.call("POST", "/api/chat", {"scope": "duck", "text": "hi", "files": bad})[0], 400)

    def say(self, text):
        status, data, _ = self.call("POST", "/api/chat", {"scope": "duck", "text": text})
        for _ in range(100):
            if not chat.busy("duck"):
                break
            time.sleep(0.02)
        return status, json.loads(data)

    def stream(self):
        return self.scope()["chat"]

    def due_now(self, text):
        r = d.add_card("duck", "reminder", "", text, "23:59")
        with d.card_store("duck", write=True) as cards:
            next(x for x in cards if x["id"] == r["id"])["at"] = time.time() - 1
        return r

    def test_a_message_gets_an_answer_in_one_session_for_the_day(self):
        self.assertEqual(self.say("recuérdame a las 18:00 llamar")[0], 200)
        self.assertEqual(self.say("y mañana lo mismo")[0], 200)
        (_, text, first), (_, _, second) = self.runs
        self.assertEqual(text, "recuérdame a las 18:00 llamar")
        self.assertEqual(first[0], "--session-id")
        self.assertEqual(second, ["--resume", first[1]])
        whos = [m["who"] for m in self.stream()]
        self.assertEqual([w for w in whos if w in ("you", "tanka")], ["you", "tanka", "you", "tanka"])

    def test_the_modules_speak_in_the_same_stream(self):
        self.due_now("Pagar la luz")
        self.t[0] = time.time()
        self.assertEqual(d.fire_reminders(), 1)
        mine = {(m.get("module"), m["who"]) for m in self.stream()}
        self.assertIn(("codepanion", "note"), mine)
        self.assertIn(("desk", "reminder"), mine)

    def test_an_answer_shows_the_cards_it_made_and_ticked(self):
        old = d.add_card("duck", "check", "app", "Pagar la luz")

        def acting(scope, text, extra):
            self.runs.append((scope, text, list(extra)))
            self.t[0] = time.time()  # the cards are made while it answers
            d.add_card("duck", "reminder", "", "Llamar", "23:59", by=d.ASSISTANT)
            d.close_card("duck", "check", "app", "Pagar la luz", by=d.ASSISTANT)
            return 0, "Listo.", ""
        chat.run_tanka = acting
        self.say("recuérdame llamar y ya pagué la luz")
        reply = [m for m in self.stream() if m["who"] == "tanka"][-1]
        kinds = {(f["module"], f["type"], f["text"]) for f in reply["effects"]}
        self.assertEqual(kinds, {("desk", "reminder", "Llamar"), ("desk", "done", "Pagar la luz")})
        self.assertEqual(next(f["id"] for f in reply["effects"] if f["type"] == "done"), old["id"])

    def test_a_fired_reminder_stays_in_the_chat_with_what_became_of_it(self):
        r = self.due_now("Pagar la luz")
        self.t[0] = time.time()
        d.fire_reminders()
        event = next(m for m in self.stream() if m["who"] == "reminder")
        self.assertEqual((event["state"], event["text"]), ("due", "Pagar la luz"))
        d.update_card("duck", r["id"], "snooze", 10)
        self.assertEqual([m["state"] for m in self.stream() if m["who"] == "reminder"], ["snoozed"])
        d.update_card("duck", r["id"], "done", 0)
        self.assertEqual([m["state"] for m in self.stream() if m["who"] == "reminder"], ["done"])

    def test_a_reply_reads_what_was_said_on_its_own(self):
        self.say("hola")
        r = self.due_now("Tomar agua")
        self.t[0] = time.time()  # it fires after "hola"
        d.fire_reminders()
        self.say("listo")
        sent = self.runs[-1][1]
        self.assertIn(r["id"], sent)
        self.assertIn("Tomar agua", sent)
        self.assertTrue(sent.endswith("The user's message:\nlisto"))
        self.say("gracias")
        self.assertEqual(self.runs[-1][1], "gracias")  # nothing new since: it goes as typed

    def test_the_brief_shows_as_things_stand(self):
        old = d.add_card("duck", "check", "app", "Revisar el PR")
        self.t[0] = time.time() + 5 * 86400  # it has been open long enough to stall
        morning = time.localtime(self.t[0])
        d.write_json(d.config_file(self.ws), {"brief": f"{max(morning.tm_hour - 1, 0):02d}:00"})
        d.daily_brief()
        d.close_card("duck", "check", "app", "Revisar el PR")
        shown = next(m for m in self.stream() if m["who"] == "brief")
        self.assertEqual([(i["id"], i["done"]) for i in shown["stale"]], [(old["id"], True)])

    def test_a_new_day_opens_with_the_end_of_the_earlier_chat(self):
        yesterday = time.time() - 86400
        kit.chat_event("duck", {"t": yesterday, "who": "you", "text": "el informe va el viernes"})
        kit.chat_event("duck", {"t": yesterday + 5, "who": "tanka", "text": "Anotado."})
        self.say("y qué quedó de ayer?")
        sent, extra = self.runs[-1][1], self.runs[-1][2]
        self.assertEqual(extra[0], "--session-id")
        self.assertIn("user: el informe va el viernes", sent)
        self.assertIn("you: Anotado.", sent)
        self.say("ok")
        self.assertEqual(self.runs[-1][1], "ok")  # resumed: the session already has it

    def test_routine_reports_show_in_the_chat_but_lens_runs_do_not(self):
        f = self.ws / ".tanka" / "reports.jsonl"
        f.write_text(json.dumps({"t": time.time(), "kind": "routine", "name": "inbox", "on": None, "exit": 0, "report": "2 correos nuevos"}) + "\n"
                     + json.dumps({"t": time.time(), "kind": "trigger", "name": "watch", "on": "codepanion:*", "exit": 0, "report": "nota"}) + "\n")
        shown = [m for m in self.stream() if m["who"] == "report"]
        self.assertEqual([(m["name"], m["text"], m["failed"]) for m in shown], [("inbox", "2 correos nuevos", False)])

    def test_the_hint_names_only_installed_tools(self):
        self.assertIn("desk_card", chat.hint("duck"))
        (self.ws / ".claude" / "desk.json").unlink()
        self.assertNotIn("desk_card", chat.hint("duck"))

    def test_a_failed_answer_offers_the_message_again(self):
        self.replies = [(1, "", "Error: Reached max turns (10)")]
        self.say("haz algo largo")
        err = [m for m in self.stream() if m["who"] == "error"][-1]
        self.assertEqual((err["code"], err["retry"]), ("noanswer", "haz algo largo"))

    def test_bad_messages_are_refused(self):
        self.assertEqual(self.say("   ")[0], 400)
        self.assertEqual(self.say("x" * 1001)[0], 400)
        self.assertEqual(self.call("POST", "/api/chat", {"scope": "duck", "text": "hola"}, token=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/chat", {"scope": "nobody", "text": "hola"})[0], 400)
        chat._busy["duck"] = time.time()
        self.addCleanup(chat._busy.pop, "duck", None)
        status, data, _ = self.call("POST", "/api/chat", {"scope": "duck", "text": "hola"})
        self.assertEqual(status, 400)
        self.assertIn("still answering", json.loads(data)["error"])
        self.assertEqual(self.runs, [])

    def test_a_message_sent_while_it_answers_stops_that_answer_and_resumes_its_session(self):
        stopped, started = threading.Event(), threading.Event()

        def slow(scope, text, extra):
            self.runs.append((scope, text, list(extra)))
            if len(self.runs) == 1:
                chat._drafts[scope] = {"text": "Revisando los correos de ayer", "tool": None}
                chat._stops[scope] = stopped.set
                started.set()
                stopped.wait(5)
                chat._stops.pop(scope, None)
                raise chat.Interrupted()
            return 0, "Ok, primero los de hoy.", ""
        self.patch(chat, "run_tanka", slow)
        self.call("POST", "/api/chat", {"scope": "duck", "text": "revisa los correos"})
        self.assertTrue(started.wait(5))
        status, data = self.say("mejor primero los de hoy")
        self.assertEqual(status, 200, data)
        (_, _, first), (_, text, second) = self.runs
        self.assertEqual(second, ["--resume", first[1]])
        self.assertTrue(text.startswith(chat.REDIRECT))
        self.assertIn("mejor primero los de hoy", text)
        said = [(m["who"], m.get("code")) for m in self.stream() if not m.get("module")]
        self.assertEqual(said[-4:], [("you", None), ("notice", "stopped"), ("you", None), ("tanka", None)])
        self.assertIn("Revisando los correos", [m for m in self.stream() if m.get("code") == "stopped"][0]["text"])

    def fake_stream(self, context):
        """A run that reports the context it read, and compacts when asked to."""
        def run(scope, text, extra):
            self.runs.append((scope, text, list(extra)))
            if text.startswith("/compact"):
                chat.on_stream(scope, {"type": "system", "subtype": "compact_boundary",
                                       "compact_metadata": {"pre_tokens": context, "post_tokens": 12000}})
                return 0, "", ""
            chat.on_stream(scope, {"type": "assistant", "message": {"content": [], "usage": {
                "input_tokens": 10, "cache_read_input_tokens": context, "cache_creation_input_tokens": 0, "output_tokens": 5}}})
            return 0, "Hecho.", ""
        self.patch(chat, "run_tanka", run)

    def test_a_long_conversation_is_compacted_before_the_next_message_keeping_the_task(self):
        self.fake_stream(chat.COMPACT_TOKENS + 1000)
        self.say("revisa los correos")
        self.assertEqual(len(self.runs), 1)  # a new session: nothing to compact yet
        self.say("sigue")
        (_, _, first), (_, compact, extra), (_, text, _) = self.runs
        self.assertTrue(compact.startswith("/compact " + chat.COMPACT_FOCUS))
        self.assertEqual(extra, ["--resume", first[1]])
        self.assertTrue(text.endswith("sigue"))
        note = [m for m in self.stream() if m.get("code") == "compacted"][0]
        self.assertIn("91k to 12k tokens", note["text"])

    def test_compact_typed_in_the_chat_compacts_with_what_the_user_adds(self):
        self.fake_stream(30000)
        self.say("/compact")
        self.assertEqual(self.runs, [])  # no conversation today yet
        self.say("hola")
        self.say("/compact keep the invoice numbers")
        compact = self.runs[-1][1]
        self.assertTrue(compact.startswith("/compact ") and compact.endswith("The user adds: keep the invoice numbers"))
        self.assertTrue([m for m in self.stream() if m.get("code") == "compacted"])
        self.assertIn("/compact", [c["name"] for c in chat.commands("duck")["tanka"]])

    def test_the_daily_cap_is_money_not_messages(self):
        tc.record_cost(self.ws, "chat", "chat", chat.DAY_USD)
        status, data = self.say("hola")
        self.assertEqual(status, 400)
        self.assertIn("TANKA_PAGE_DAY_USD", data["error"])
        self.assertEqual(self.runs, [])

    def test_a_message_to_the_other_chat_waits(self):
        chat._busy["duck"], chat._busy_mode["duck"] = time.time(), "dev"
        chat._stops["duck"] = lambda: self.fail("dev was stopped")
        for d in (chat._busy, chat._busy_mode, chat._stops):
            self.addCleanup(d.pop, "duck", None)
        status, data, _ = self.call("POST", "/api/chat", {"scope": "duck", "text": "hola"})
        self.assertEqual(status, 400)
        self.assertIn("Dev is still answering", json.loads(data)["error"])

    def test_a_failed_run_says_so_and_a_lost_session_starts_again(self):
        self.replies = [(1, "", "Error: Reached max turns (10)")]
        self.say("haz algo largo")
        last = [m for m in self.stream() if not m.get("module")][-1]
        self.assertEqual(last["who"], "error")
        self.assertIn("max turns", last["text"])
        self.assertFalse(chat.session_file("duck").exists())  # nothing was kept, so the next one starts fresh
        self.say("hola")
        self.replies = [(1, "", "No conversation found with session ID"), (0, "Hola de nuevo.", "")]
        self.say("sigues ahí?")
        resumed, again = self.runs[-2][2], self.runs[-1][2]
        self.assertEqual(resumed[0], "--resume")
        self.assertEqual(again[0], "--session-id")
        self.assertNotEqual(again[1], resumed[1])
        self.assertEqual([m for m in self.stream() if not m.get("module")][-1]["text"], "Hola de nuevo.")


class TestDev(PageCase):
    """The selector's other side: dev, with its own session, permissions and spending cap."""

    def setUp(self):
        super().setUp()
        self.runs = []

        def fake(kind):
            def run(scope, text, extra):
                self.runs.append((kind, text, list(extra)))
                return 0, f"{kind} answered", ""
            return run
        self.patch(chat, "run_tanka", fake("tanka"))
        self.patch(chat, "run_dev", fake("dev"))

    def say(self, text, mode="tanka"):
        status, data, _ = self.call("POST", "/api/chat", {"scope": "duck", "text": text, "mode": mode})
        for _ in range(100):
            if not chat.busy("duck"):
                break
            time.sleep(0.02)
        return status, json.loads(data)

    def test_dev_has_its_own_session_and_conversation(self):
        self.say("hola")
        self.say("crea un tablero de clientes", mode="dev")
        self.say("y ahora?", mode="dev")
        self.say("gracias")
        kinds = [(k, extra[0]) for k, _, extra in self.runs]
        self.assertEqual(kinds, [("tanka", "--session-id"), ("dev", "--session-id"), ("dev", "--resume"), ("tanka", "--resume")])
        self.assertNotEqual(self.runs[0][2][1], self.runs[1][2][1])
        lines = [(m["who"], m.get("mode")) for m in self.scope()["chat"] if not m.get("module")]
        self.assertEqual(lines, [("you", None), ("tanka", None), ("you", "dev"), ("dev", None), ("you", "dev"), ("dev", None),
                                 ("you", None), ("tanka", None)])
        self.assertEqual(chat.sent_today("duck"), 2)  # dev is capped by money, not by messages

    def test_a_new_day_recaps_each_conversation_apart(self):
        yesterday = time.time() - 86400
        kit.chat_event("duck", {"t": yesterday, "who": "you", "text": "el informe va el viernes"})
        kit.chat_event("duck", {"t": yesterday + 1, "who": "you", "text": "agrega un estado al tablero", "mode": "dev"})
        kit.chat_event("duck", {"t": yesterday + 2, "who": "dev", "text": "Listo, agregué el estado."})
        self.say("qué quedó?", mode="dev")
        sent = self.runs[-1][1]
        self.assertIn("agrega un estado al tablero", sent)
        self.assertNotIn("el informe va el viernes", sent)

    def test_dev_stops_at_its_daily_cap(self):
        kit.tc.record_cost(self.ws, "dev", "dev", chat.DEV_DAY_USD)
        status, data = self.say("otro tablero", mode="dev")
        self.assertEqual(status, 400)
        self.assertIn("for today", data["error"])
        self.assertEqual(self.say("hola")[0], 200)  # the assistant is not affected
        self.assertEqual(self.say("x", mode="root")[0], 400)

    def test_the_dev_command_is_guarded_by_its_own_hook(self):
        cmd = chat.dev_cmd("duck", "hola", ["--session-id", "s"])
        joined = " ".join(cmd)
        hook = json.loads(cmd[cmd.index("--settings") + 1])["hooks"]["PreToolUse"][0]
        self.assertEqual(hook["matcher"], "*")
        self.assertIn("tanka_dev_guard.py", hook["hooks"][0]["command"])
        self.assertIn(str(self.ws.resolve()), hook["hooks"][0]["command"])
        self.assertIn("--setting-sources project,local", joined)  # none of the user's hooks or MCP servers
        self.assertIn(f"--max-budget-usd {chat.DEV_BUDGET_USD:.2f}", joined)
        self.assertIn("WebFetch", cmd[cmd.index("--disallowedTools") + 1:])

    def test_state_says_what_dev_may_still_spend(self):
        kit.tc.record_cost(self.ws, "dev", "dev", 1.5)
        dev = self.scope()["dev"]
        self.assertEqual(dev["left_usd"], round(chat.DEV_DAY_USD - 1.5, 2))


class TestStreaming(WorkspaceCase):
    """The chat's run as it streams: the draft the page shows, and the answer at the end."""

    def fake_bin(self, lines, code=0):
        script = self.tmp / "tanka"
        out = "\n".join(json.dumps(x) for x in lines)
        script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{out}\nEOF\nexit {code}\n")
        script.chmod(0o755)
        self.patch(chat, "TANKA_BIN", script)

    def test_the_answer_comes_from_the_result_and_the_draft_follows_the_text(self):
        self.fake_bin([{"type": "stream_event", "event": {"type": "message_start"}},
                       {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Lis"}}},
                       {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__tanka__desk_card"}]}},
                       {"type": "result", "result": "Listo: a las 18:00.", "is_error": False, "total_cost_usd": 0.012}])
        self.assertEqual(chat.run_tanka("duck", "hola", []), (0, "Listo: a las 18:00.", ""))
        self.assertEqual(chat._drafts.pop("duck"), {"text": "Lis", "tool": "mcp__tanka__desk_card"})
        self.assertEqual(kit.tc.spent_today(self.ws), (0.012, 1))

    def test_a_resumed_session_counts_only_what_this_answer_cost(self):
        # Claude Code reports a resumed session's total so far, not the answer's own cost.
        self.fake_bin([{"type": "result", "result": "uno", "total_cost_usd": 0.05}])
        chat.run_tanka("duck", "hola", ["--session-id", "s-1"])
        self.fake_bin([{"type": "result", "result": "dos", "total_cost_usd": 0.08}])
        chat.run_tanka("duck", "y?", ["--resume", "s-1"])
        chat._drafts.pop("duck", None)
        usd, runs = kit.tc.spent_today(self.ws)
        self.assertEqual((round(usd, 4), runs), (0.08, 2))

    def test_a_run_that_ends_in_error_has_no_answer(self):
        self.fake_bin([{"type": "result", "subtype": "error_max_turns", "is_error": True}], code=1)
        code, out, err = chat.run_tanka("duck", "hola", [])
        chat._drafts.pop("duck", None)
        self.assertEqual((code, out, err), (1, "", "error_max_turns"))


class TestNames(WorkspaceCase):
    def test_the_page_uses_the_persona_name(self):
        self.assertEqual(kit.persona_name(self.ws), "duck")
        (self.ws / ".tanka" / "persona.json").write_text(json.dumps({"name": "Nova"}))
        self.assertEqual(kit.persona_name(self.ws), "Nova")
        self.assertEqual(page.scope_state("duck")["name"], "Nova")


if __name__ == "__main__":
    unittest.main()
