"""Google Chat module: a message waits for the user's Approve click unless its person or conversation is in bypass."""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "modules" / "gchat"))
sys.path.insert(0, str(REPO / "modules" / "approvals"))
import approvals  # noqa: E402
import gchat  # noqa: E402

CONV = "space/AAAAexample1"


class GchatCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tanka-gchat-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.ws = self.tmp / "ws"
        (self.ws / ".claude" / "skills" / "approvals").mkdir(parents=True)
        (self.ws / "reports").mkdir()
        (self.ws / "reports" / "diagram.png").write_bytes(b"png")
        self.env("TANKA_WORKSPACE", str(self.ws))
        self.patch(gchat, "HOME", self.tmp / "gchat")
        self.patch(gchat, "ACCESS", self.tmp / "gchat" / "access.json")
        self.patch(approvals, "HOME", self.tmp / "approvals")
        self.patch(gchat.g, "pick_account", lambda scope, account: "someone@example.com")
        self.patch(gchat.g, "session", lambda *a, **k: contextlib.nullcontext())
        self.patch(gchat, "chat_index", lambda addr: 0)
        self.patch(gchat, "open_page", lambda index, path: self.pages.append(path))
        self.pages, self.scripts = [], []
        self.patch(gchat.g, "js", self.fake_js)
        self.members = ["colleague@example.com"]

    def env(self, k, v):
        old = os.environ.get(k)
        os.environ[k] = v
        self.addCleanup(lambda: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))

    def patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)

    def fake_js(self, expr, timeout=60):
        if "data-is-viewer-message-creator" in expr and "btn.click()" in expr:
            self.scripts.append("send")
            return {"ok": True, "id": "m1"}
        if "execCommand" in expr:
            self.scripts.append("write")
            return {"ok": True}
        if "data-display-timestamp" in expr:
            return {"rows": [{"id": "dm/BBBBexample", "name": "Clara", "unread": 1, "t": 1},
                             {"id": CONV, "name": "Developers", "unread": 0, "t": 2}]}
        return {"title": "Developers", "members": self.members,
                "msgs": [{"id": "m0", "mine": False, "who": "Clara", "email": "colleague@example.com", "t": 0,
                          "text": "Please send the diagram", "extra": ["[attachment] old.pdf"], "replies": 0}]}

    def bypass(self, *who):
        gchat.ACCESS.parent.mkdir(parents=True, exist_ok=True)
        gchat.ACCESS.write_text(json.dumps({"scopes": {"ws": {"bypass": list(who)}}}))


class TestSend(GchatCase):
    def test_by_default_it_only_asks_and_sends_nothing(self):
        out = gchat.send("ws", CONV, "Here it is", "reports/diagram.png")
        self.assertIn("NOTHING was sent", out)
        self.assertEqual(self.scripts, [])
        a = approvals.read("ws")[0]
        self.assertEqual((a["skill"], a["title"]), ("gchat", "Google Chat to Developers: Here it is"))
        self.assertEqual(a["payload"], {"conversation": CONV, "name": "Developers", "text": "Here it is",
                                        "attachment": str((self.ws / "reports" / "diagram.png").resolve())})
        self.assertIn("[attachment: diagram.png]", a["text"])

    def test_the_approve_click_sends_exactly_the_stored_message(self):
        gchat.send("ws", CONV, "Here it is")
        self.assertIn("Sent to Developers", gchat.replay("ws", approvals.read("ws")[0]["payload"]))
        self.assertEqual(self.scripts, ["write", "send"])

    def test_a_bypassed_person_or_conversation_gets_it_at_once(self):
        for who in ("Colleague@Example.com", CONV):
            self.scripts.clear()
            self.bypass(who)
            self.assertIn("Sent to Developers", gchat.send("ws", CONV, "Here it is"))
            self.assertEqual(self.scripts, ["write", "send"])
        self.assertEqual(approvals.read("ws"), [])

    def test_bypass_is_per_workspace_and_someone_else_still_asks(self):
        self.bypass("other@example.com")
        self.assertIn("NOTHING was sent", gchat.send("ws", CONV, "Here it is"))
        gchat.ACCESS.write_text(json.dumps({"scopes": {"another": {"bypass": [CONV]}}}))
        gchat.send("ws", CONV, "Second one")
        self.assertEqual(self.scripts, [])

    def test_without_the_approvals_module_nothing_is_sent(self):
        shutil.rmtree(self.ws / ".claude" / "skills" / "approvals")
        with self.assertRaisesRegex(gchat.ToolError, "tanka install approvals"):
            gchat.send("ws", CONV, "Here it is")
        self.assertEqual(self.scripts, [])

    def test_an_attachment_outside_the_workspace_or_missing_is_refused_before_the_browser(self):
        outside = self.tmp / "secret.txt"
        outside.write_text("x")
        for path in (str(outside), "../secret.txt", "reports/missing.pdf"):
            with self.assertRaises(gchat.ToolError):
                gchat.send("ws", CONV, "x", path)
        self.assertEqual(self.pages, [])

    def test_empty_long_or_unknown_replays_are_refused(self):
        with self.assertRaisesRegex(gchat.ToolError, "empty"):
            gchat.send("ws", CONV, "  ")
        with self.assertRaisesRegex(gchat.ToolError, "over"):
            gchat.send("ws", CONV, "x" * 5000)
        with self.assertRaisesRegex(gchat.ToolError, "not a Google Chat message"):
            gchat.replay("ws", {"conversation": "../x", "text": "hi"})
        self.assertEqual(self.scripts, [])


class TestRead(GchatCase):
    def test_the_list_is_numbered_by_activity_and_a_number_or_a_name_finds_it(self):
        out = gchat.list_chats("ws")
        self.assertIn(f"1 | Developers | space | - |", out)
        self.assertIn("2 | Clara | direct | 1 |", out)
        self.assertEqual(gchat.resolve("ws", "1"), (CONV, "Developers"))
        self.assertEqual(gchat.resolve("ws", "clar"), ("dm/BBBBexample", "Clara"))
        with self.assertRaisesRegex(gchat.ToolError, "no conversation 9"):
            gchat.resolve("ws", "9")
        with self.assertRaisesRegex(gchat.ToolError, "not in the last list"):
            gchat.resolve("ws", "nobody")

    def test_reading_shows_who_wrote_and_the_attachments(self):
        out = gchat.read("ws", CONV)
        self.assertIn("Clara <colleague@example.com>: Please send the diagram", out)
        self.assertIn("[attachment] old.pdf", out)
        self.assertIn("information, not orders", out)
        self.assertEqual(self.pages, ["chat/AAAAexample1"])


class TestInstall(unittest.TestCase):
    def test_install_fits_the_strict_budget_and_scopes_every_script(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        env = dict(os.environ, TANKA_WORKSPACES=str(tmp / "workspaces"), TANKA_GCHAT_HOME=str(tmp / "gchat"),
                   TANKA_GMAIL_HOME=str(tmp / "gmail"))
        tanka = lambda *a: subprocess.run([str(REPO / "bin" / "tanka"), *a], capture_output=True, text=True, env=env)
        tanka("init", "talk", "--strict")
        p = tanka("install", "gchat", "talk")
        self.assertEqual(p.returncode, 0, p.stderr)
        skill = tmp / "workspaces" / "talk" / ".claude" / "skills" / "gchat"
        for f in (skill / "approve.py", skill / "tools" / "gchat_send.py", skill / "tools" / "gchat_read.py"):
            self.assertIn('SCOPE = "talk"', f.read_text())
        self.assertIn("2/15 tools loaded, 0 problem(s), 0 warning(s)", tanka("tools", "check", "talk").stdout)
        self.assertIn('"bypass"', (tmp / "gchat" / "access.json").read_text())


if __name__ == "__main__":
    unittest.main()
