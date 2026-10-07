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


class TestLogin(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.saved = {k: os.environ.pop(k, None) for k in ("TANKA_BROWSER", "TANKA_WHATSAPP_BROWSER", "RASTRO_HOME", "TANKA_GMAIL_PROFILE")}
        self.addCleanup(lambda: [os.environ.__setitem__(k, v) for k, v in self.saved.items() if v is not None])

    def test_the_browser_is_the_one_the_user_named_if_it_runs(self):
        fake = self.tmp / "browser"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)
        os.environ["TANKA_BROWSER"] = str(fake)
        self.assertEqual(gmail.real_browser(), str(fake))

    def test_no_browser_says_how_to_fix_it(self):
        os.environ["TANKA_BROWSER"] = str(self.tmp / "nope")
        old = gmail.BROWSERS
        gmail.BROWSERS = ()
        self.addCleanup(setattr, gmail, "BROWSERS", old)
        with self.assertRaisesRegex(gmail.ToolError, "TANKA_BROWSER"):
            gmail.real_browser()

    def test_the_profile_follows_rastro_home_and_the_session_name(self):
        os.environ["RASTRO_HOME"] = str(self.tmp)
        self.assertEqual(gmail.profile_dir(), self.tmp / "profiles" / gmail.SESSION)
        os.environ["TANKA_GMAIL_PROFILE"] = str(self.tmp / "mine")
        self.assertEqual(gmail.profile_dir(), self.tmp / "mine")

    def test_login_opens_a_normal_window_on_that_profile_and_never_the_automated_one(self):
        from unittest import mock
        sys.path.insert(0, str(REPO / "modules" / "gmail"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("gmail_cli", REPO / "modules" / "gmail" / "cli.py")
        cli = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cli)
        os.environ["TANKA_GMAIL_PROFILE"] = str(self.tmp / "profile")
        window = mock.Mock()
        window.wait.return_value = 0
        with mock.patch.object(gmail, "running", return_value=True), \
             mock.patch.object(gmail, "real_browser", return_value="/bin/browser"), \
             mock.patch.object(gmail, "rastro") as rastro, \
             mock.patch.object(cli.subprocess, "Popen", return_value=window) as popen, \
             mock.patch("builtins.input", return_value=""), \
             mock.patch.object(cli, "status", return_value=0):
            self.assertEqual(cli.login(), 0)
        rastro.assert_called_once_with("close", timeout=60)  # the automated session is closed first, never opened headed
        argv = popen.call_args.args[0]
        self.assertEqual(argv[0], "/bin/browser")
        self.assertIn(f"--user-data-dir={self.tmp / 'profile'}", argv)
        self.assertNotIn("--remote-debugging-pipe", " ".join(argv))
        window.terminate.assert_called_once()


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
