"""WhatsApp on the page: the People tab's data, the user's changes to contacts.json, and that a workspace
only ever sees its own roles. Every home is temporary and the browser is never touched."""
from __future__ import annotations

import http.client
import json
import os
import stat
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from workspace_case import REPO, WorkspaceCase, kit  # noqa: E402

sys.path.insert(0, str(REPO / "modules" / "whatsapp"))
import tanka_chat as chat  # noqa: E402
import tanka_page as page  # noqa: E402
import wa  # noqa: E402

RECORD = """---
name: Clara Client
company: Corner Bakery
sale_status: quoted
---
2026-09-28: asked for a quote.
2026-09-30: wants the logo in green.
"""


def no_browser(*args, **kwargs):
    raise AssertionError("the browser must not be used")


class WhatsAppCase(WorkspaceCase):
    """sales (the skill installed, roles client and lead), school (no skill, role student), shop (the skill,
    no role) and duck (neither), with one contacts.json shared by all."""

    def setUp(self):
        super().setUp()
        self.wa_home = self.tmp / "wa"
        self.wa_home.mkdir()
        self.patch(wa, "HOME", self.wa_home)
        self.patch(wa, "REGISTRY", self.wa_home / "contacts.json")
        self.patch(wa, "LOCK", self.wa_home / ".lock")
        self.patch(wa, "session", no_browser)
        self.patch(wa, "rastro", no_browser)
        for name in ("sales", "school", "shop"):
            (self.wsdir / name / ".tanka").mkdir(parents=True)
            (self.wsdir / name / ".tanka" / "policy.json").write_text("{}")
        for name in ("sales", "shop"):
            (self.wsdir / name / ".claude" / "skills" / "whatsapp").mkdir(parents=True)
        self.sales, self.school = self.wsdir / "sales", self.wsdir / "school"
        people = self.sales / "notes" / "people"
        people.mkdir(parents=True)
        (people / "15551234567.md").write_text(RECORD)
        (people / "15553334444.md").write_text("---\nsale_status: won\n---\nPete's record, kept in the wrong place.\n")
        self.write_registry({
            "version": 1,
            "roles": {"client": {"workspace": "sales", "read": True, "reply": True, "instructions": "Be brief.", "color": "green"},
                      "lead": {"workspace": "sales", "read": True, "reply": False},
                      "student": {"workspace": "school", "read": True, "reply": True}},
            "contacts": {"+1 555 123 4567": {"name": "Clara Client", "role": "client", "note": "pays late"},
                         "+15559876543": {"name": "Leo Lead", "role": "lead"},
                         "+15553334444": {"name": "Pete Student", "role": "student"},
                         "+15550001111": {"name": "Old Friend", "role": "friend"}}})
        self.wa = chat.hooks()["whatsapp"]

    def write_registry(self, data):
        wa.REGISTRY.write_text(json.dumps(data, indent=2))
        os.chmod(wa.REGISTRY, 0o600)

    def registry(self) -> dict:
        return json.loads(wa.REGISTRY.read_text())

    def act(self, scope, name, /, **body):
        return self.wa.ACTIONS[name](scope, kit.ws_dir(scope), body)

    def get(self, scope, name, /, **q):
        return self.wa.GETS[name](scope, kit.ws_dir(scope), q)

    def refused(self, pattern, scope, name, /, **body):
        before = wa.REGISTRY.read_bytes()
        with self.assertRaisesRegex(kit.ToolError, pattern):
            self.act(scope, name, **body)
        self.assertEqual(wa.REGISTRY.read_bytes(), before, "a refusal must not write")


class TestInstalled(WhatsAppCase):
    def test_by_skill_by_role_and_neither(self):
        self.assertTrue(self.wa.installed(self.sales))
        self.assertTrue(self.wa.installed(self.school))  # a role names it, no skill
        self.assertTrue(self.wa.installed(self.wsdir / "shop"))  # the skill, no role
        self.assertFalse(self.wa.installed(self.ws))

    def test_a_workspace_reached_through_a_symlink_has_its_roles(self):
        real = self.tmp / "elsewhere" / "folder"
        real.mkdir(parents=True)
        (self.wsdir / "school").rename(real)
        (self.wsdir / "school").symlink_to(real)  # the role says "school"; the folder is called "folder"
        self.assertTrue(self.wa.installed(kit.ws_dir("school")))

    def test_a_missing_or_broken_registry_does_not_crash(self):
        wa.REGISTRY.write_text("{not json")
        self.assertFalse(self.wa.installed(self.school))
        self.assertTrue(self.wa.installed(self.sales))
        self.assertEqual(self.wa.state("school", self.school)["roles"], [])
        self.assertIn("not valid JSON", self.wa.state("school", self.school)["problem"])
        wa.REGISTRY.unlink()
        self.assertFalse(self.wa.installed(self.school))
        self.assertIn("does not exist", self.wa.state("sales", self.sales)["problem"])
        self.assertEqual(self.wa.health()[0]["text"], "no contacts.json")
        self.assertFalse(wa.LOCK.exists())  # reading never creates the session lock


class TestReading(WhatsAppCase):
    def test_state_has_only_this_workspaces_roles(self):
        st = self.wa.state("sales", self.sales)
        self.assertIsNone(st["problem"])
        self.assertEqual([r["name"] for r in st["roles"]], ["client", "lead"])
        client = st["roles"][0]
        self.assertEqual((client["read"], client["reply"], client["auto_reply"], client["instructions"], client["count"]),
                         (True, True, False, True, 1))
        self.assertEqual([r["name"] for r in self.wa.state("school", self.school)["roles"]], ["student"])

    def test_people_never_shows_another_workspace(self):
        res = self.get("sales", "people")
        self.assertEqual([p["name"] for p in res["people"]], ["Clara Client", "Leo Lead"])
        clara = res["people"][0]
        self.assertEqual(clara["number"], "+15551234567")
        self.assertEqual(clara["fields"]["sale_status"], "quoted")
        self.assertEqual((clara["notes"], clara["last_note"]), (2, "2026-09-30"))
        self.assertEqual(res["people"][1]["fields"], {})
        self.assertEqual(res["fields"], ["company", "sale_status"])
        self.assertNotIn("Pete", json.dumps(res))
        self.assertNotIn("Old Friend", json.dumps(res))
        school = self.get("school", "people")
        self.assertEqual([p["name"] for p in school["people"]], ["Pete Student"])
        self.assertEqual(school["people"][0]["fields"], {})  # records are read from its own workspace only

    def test_person_only_for_this_workspace(self):
        rec = self.get("sales", "person", number="+1 (555) 123-4567")
        self.assertEqual(rec["fields"]["company"], "Corner Bakery")
        self.assertIn("logo in green", rec["notes"])
        # parse_qs turns a "+" in a query into a space
        self.assertEqual(self.get("sales", "person", number=" 15551234567")["name"], "Clara Client")
        for num in ("+15553334444", "+15550001111", "+15550000000"):
            with self.assertRaisesRegex(kit.ToolError, "not one of this workspace's contacts"):
                self.get("sales", "person", number=num)

    def test_health(self):
        row = self.wa.health()[0]
        self.assertIn("4 contact(s), 3 role(s)", row["text"])


class TestActions(WhatsAppCase):
    def test_set_role(self):
        self.act("sales", "set_role", number="+15559876543", role="client")
        self.assertEqual(self.registry()["contacts"]["+15559876543"]["role"], "client")
        self.refused("not a role of this workspace", "sales", "set_role", number="+15559876543", role="student")
        self.refused("not one of this workspace's contacts", "sales", "set_role", number="+15553334444", role="client")
        self.refused("not one of this workspace's contacts", "sales", "set_role", number="+15550001111", role="client")

    def test_add(self):
        res = self.act("sales", "add", number="+1 (555) 222-3333", name="  New   Client ", role="client")
        self.assertEqual(res["number"], "+15552223333")
        self.assertEqual(self.registry()["contacts"]["+15552223333"], {"name": "New Client", "role": "client"})
        self.refused("8 to 15 digits", "sales", "add", number="555-1234", name="X", role="client")
        self.refused("8 to 15 digits", "sales", "add", number="+1234567890123456", name="X", role="client")
        self.refused("belongs to another workspace", "sales", "add", number="+15553334444", name="Pete", role="client")
        self.refused("already a contact of this workspace", "sales", "add", number="15551234567", name="C", role="lead")
        self.refused("not a role of this workspace", "sales", "add", number="+15557778888", name="X", role="student")
        self.refused("1 to 60 characters", "sales", "add", number="+15557778888", name=" ", role="client")
        self.refused("1 to 60 characters", "sales", "add", number="+15557778888", name="x" * 61, role="client")

    def test_add_takes_a_number_whose_role_is_gone(self):
        self.act("sales", "add", number="+15550001111", name="Old Friend", role="lead")
        self.assertEqual(self.registry()["contacts"]["+15550001111"]["role"], "lead")

    def test_rename_keeps_the_rest(self):
        self.act("sales", "rename", number="15551234567", name="Clara C.")
        self.assertEqual(self.registry()["contacts"]["+1 555 123 4567"],
                         {"name": "Clara C.", "role": "client", "note": "pays late"})
        self.refused("1 to 60 characters", "sales", "rename", number="15551234567", name="")
        self.refused("not one of this workspace's contacts", "sales", "rename", number="+15553334444", name="Pete")

    def test_remove(self):
        self.refused("not one of this workspace's contacts", "school", "remove", number="+15551234567")
        self.act("sales", "remove", number="+15551234567")
        contacts = self.registry()["contacts"]
        self.assertNotIn("+1 555 123 4567", contacts)
        self.assertIn("+15553334444", contacts)

    def test_role_flags(self):
        self.refused("the assistant will answer these people without asking you", "sales", "role_flags",
                     role="client", auto_reply=True)
        self.refused("the assistant will answer these people without asking you", "sales", "role_flags",
                     role="client", auto_reply=True, confirm="yes")
        self.act("sales", "role_flags", role="client", auto_reply=True, confirm=True)
        self.assertTrue(self.registry()["roles"]["client"]["auto_reply"])
        self.act("sales", "role_flags", role="client", read=False)  # already on: no confirm needed again
        self.assertEqual(self.registry()["roles"]["client"]["auto_reply"], True)
        self.act("sales", "role_flags", role="client", reply=False)
        client = self.registry()["roles"]["client"]
        self.assertEqual((client["read"], client["reply"], client["auto_reply"]), (False, False, False))
        self.assertEqual((client["instructions"], client["color"]), ("Be brief.", "green"))
        # Reply off and auto_reply on in one request: auto_reply stays off, so there is nothing to confirm.
        self.act("sales", "role_flags", role="lead", reply=False, auto_reply=True)
        self.assertFalse(self.registry()["roles"]["lead"]["auto_reply"])
        self.refused("not a role of this workspace", "sales", "role_flags", role="student", read=False)
        self.refused("true or false", "sales", "role_flags", role="client", read="false")

    def test_flags_are_read_as_wa_reads_them(self):
        data = self.registry()
        data["roles"]["lead"].update(read=1, auto_reply="yes", reply=True)
        self.write_registry(data)
        lead = self.wa.state("sales", self.sales)["roles"][1]
        self.assertEqual((lead["read"], lead["auto_reply"]), (True, True))
        self.act("sales", "role_flags", role="lead", auto_reply=True)  # already on as wa reads it: no confirm
        self.act("sales", "role_flags", role="lead", reply=False)
        lead = self.registry()["roles"]["lead"]
        self.assertEqual((lead["read"], lead["reply"], lead["auto_reply"]), (1, False, False))  # read untouched

    def test_writes_are_atomic_and_keep_unknown_keys(self):
        self.act("sales", "set_role", number="+15559876543", role="client")
        data = self.registry()  # valid JSON
        self.assertEqual(data["version"], 1)
        self.assertEqual(data["roles"]["student"], {"workspace": "school", "read": True, "reply": True})
        self.assertIn("+1 555 123 4567", data["contacts"])
        self.assertEqual(stat.S_IMODE(wa.REGISTRY.stat().st_mode), 0o600)
        self.assertEqual(sorted(p.name for p in self.wa_home.iterdir()), [".contacts.lock", "contacts.json"])
        self.assertFalse(wa.LOCK.exists())  # the session lock is not the registry's

    def test_wa_refusals_reach_the_page_as_tool_errors(self):
        wa.REGISTRY.unlink()
        with self.assertRaisesRegex(kit.ToolError, "does not exist"):
            self.act("sales", "remove", number="+15551234567")
        with self.assertRaisesRegex(kit.ToolError, "does not exist"):
            self.get("sales", "people")


class TestOnThePage(WhatsAppCase):
    def setUp(self):
        super().setUp()
        self.srv = page.make_server()
        self.port = self.srv.server_address[1]
        self.token = self.srv.RequestHandlerClass.token
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def call(self, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": f"127.0.0.1:{self.port}", "X-Tanka-Token": self.token}
        if body is not None:
            headers["Content-Type"] = "application/json"
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        status, data = r.status, r.read()
        conn.close()
        return status, data

    def test_the_tab_shows_in_the_right_workspaces(self):
        status, body = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        mods = {s["scope"]: s["modules"] for s in json.loads(body)["scopes"]}
        self.assertEqual([r["name"] for r in mods["sales"]["whatsapp"]["roles"]], ["client", "lead"])
        self.assertEqual([r["name"] for r in mods["school"]["whatsapp"]["roles"]], ["student"])
        self.assertEqual(mods["shop"]["whatsapp"]["roles"], [])
        self.assertNotIn("whatsapp", mods["duck"])
        status, html = self.call("GET", f"/?t={self.token}")
        self.assertIn('Tanka.module("whatsapp"', html.decode())

    def test_gets_and_actions_over_http(self):
        status, body = self.call("GET", "/api/m/whatsapp/people?scope=school")
        self.assertEqual((status, [p["name"] for p in json.loads(body)["people"]]), (200, ["Pete Student"]))
        status, body = self.call("GET", "/api/m/whatsapp/person?scope=school&number=+15551234567")
        self.assertEqual(status, 400)
        status, body = self.call("POST", "/api/m/whatsapp/add", {"scope": "school", "number": "+15551234567",
                                                                 "name": "Clara", "role": "student"})
        self.assertEqual(status, 400)
        self.assertIn("belongs to another workspace", json.loads(body)["error"])
        status, _ = self.call("POST", "/api/m/whatsapp/remove", {"scope": "duck", "number": "+15553334444"})
        self.assertEqual(status, 404)  # not installed there
        status, _ = self.call("POST", "/api/m/whatsapp/remove", {"scope": "school", "number": "+15553334444"})
        self.assertEqual(status, 200)
        self.assertNotIn("+15553334444", self.registry()["contacts"])


if __name__ == "__main__":
    unittest.main()
