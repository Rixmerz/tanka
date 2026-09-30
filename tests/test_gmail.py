"""Gmail module: account scoping, MIME parsing and the attachment folder rule, without a browser."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from email.message import EmailMessage
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "modules" / "gmail"))
import gmail  # noqa: E402


class GmailCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "accounts.json").write_text(json.dumps({"accounts": {
            "Me@Example.com": {"workspace": "personal"},
            "office@example.com": {"workspace": "work"},
            "second@example.com": {"workspace": "work"}}}))
        for name, value in {"REGISTRY": self.tmp / "accounts.json", "HOME": self.tmp}.items():
            old = getattr(gmail, name)
            setattr(gmail, name, value)
            self.addCleanup(setattr, gmail, name, old)
        self.ws = self.tmp / "ws"
        self.ws.mkdir()
        os.environ["TANKA_WORKSPACE"] = str(self.ws)
        self.addCleanup(os.environ.pop, "TANKA_WORKSPACE", None)
        old = gmail.session
        gmail.session = lambda: (_ for _ in ()).throw(AssertionError("the browser must not be used"))
        self.addCleanup(setattr, gmail, "session", old)


class TestAccountScope(GmailCase):
    def test_single_mailbox_is_the_default(self):
        self.assertEqual(gmail.pick_account("personal", None), "me@example.com")

    def test_several_mailboxes_need_a_choice(self):
        with self.assertRaisesRegex(gmail.ToolError, "Ask the user which one"):
            gmail.pick_account("work", None)
        self.assertEqual(gmail.pick_account("work", "second"), "second@example.com")

    def test_other_workspace_mailbox_is_refused(self):
        with self.assertRaisesRegex(gmail.ToolError, "not a mailbox this assistant may read"):
            gmail.pick_account("personal", "office@example.com")

    def test_workspace_without_mailbox(self):
        with self.assertRaisesRegex(gmail.ToolError, "No mailbox is assigned"):
            gmail.pick_account("nobody", None)

    def test_refusals_happen_before_the_browser(self):
        with self.assertRaisesRegex(gmail.ToolError, "not a mailbox"):
            gmail.send_mail("personal", "office@example.com", "a@example.com", "s", "m", None)
        with self.assertRaisesRegex(gmail.ToolError, "no message number 4"):
            gmail.reply("personal", 4, "hi", None)


class TestAttachmentFolder(GmailCase):
    def test_inside_the_workspace(self):
        f = self.ws / "quote.pdf"
        f.write_bytes(b"%PDF")
        self.assertEqual(gmail.check_attachment(str(f)), f.resolve())

    def test_outside_is_refused(self):
        outside = self.tmp / "secret.txt"
        outside.write_text("x")
        with self.assertRaisesRegex(gmail.ToolError, "outside the folders"):
            gmail.check_attachment(str(outside))
        with self.assertRaisesRegex(gmail.ToolError, "outside the folders"):
            gmail.check_attachment(str(self.ws / ".." / "secret.txt"))

    def test_missing_file(self):
        with self.assertRaisesRegex(gmail.ToolError, "does not exist"):
            gmail.check_attachment(str(self.ws / "nope.pdf"))


class TestParse(GmailCase):
    def message(self) -> str:
        m = EmailMessage()
        m["From"] = "Clara Client <clara@example.com>"
        m["To"] = "me@example.com"
        m["Subject"] = "Quote request"
        m["Date"] = "Tue, 29 Sep 2026 21:50:56 +0000"
        m.set_content("Hi, the brief is attached.")
        m.add_alternative("<p>Hi, the <b>brief</b> is attached.</p>", subtype="html")
        m.add_attachment(b"PK\x03\x04zipdata", maintype="application", subtype="zip", filename="brief.zip")
        m.add_attachment(b"\x89PNG" + b"0" * 100, maintype="image", subtype="png", filename="logo.png", disposition="inline")
        return m.as_string()

    def test_headers_body_and_attachments(self):
        out = gmail.parse(self.message(), self.ws / "gmail" / "x")
        self.assertEqual(out["subject"], "Quote request")
        self.assertIn("clara@example.com", out["from"])
        self.assertEqual(out["body"], "Hi, the brief is attached.")
        self.assertEqual(len(out["saved"]), 1)  # the tiny inline logo is a signature, not content
        self.assertEqual((self.ws / "gmail" / "x" / "brief.zip").read_bytes(), b"PK\x03\x04zipdata")


class TestInstall(unittest.TestCase):
    def test_install_and_check(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        env = dict(os.environ, TANKA_WORKSPACES=str(tmp / "workspaces"), TANKA_GMAIL_HOME=str(tmp / "home"))
        tanka = lambda *a: subprocess.run([str(REPO / "bin" / "tanka"), *a], capture_output=True, text=True, env=env)
        tanka("init", "mail")
        p = tanka("install", "gmail", "mail")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((tmp / "home" / "accounts.json").is_file())
        self.assertIn('SCOPE = "mail"', (tmp / "workspaces" / "mail" / ".claude/skills/gmail/tools/gmail_reply.py").read_text())
        self.assertIn("4/15 tools loaded, 0 problem(s), 0 warning(s)", tanka("tools", "check", "mail").stdout)


if __name__ == "__main__":
    unittest.main()
