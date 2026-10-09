"""tanka-link: the listener the plugin runs in every Claude Code session, and Tanka's side (sessions, send)."""
from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LISTEN = REPO / "link" / "listen.py"
sys.path.insert(0, str(REPO / "modules" / "link"))
import link  # noqa: E402

SID = "0f8a7c2e-1111-4222-8333-944455556666"


class LinkCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="tanka-link-"))
        old = link.HOME
        link.HOME = self.home
        self.addCleanup(setattr, link, "HOME", old)
        self.env = {**os.environ, "TANKA_LINK_HOME": str(self.home)}

    def listener(self, *args):
        p = subprocess.Popen([sys.executable, str(LISTEN), *args], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, env=self.env)
        p.stdin.write(json.dumps({"session_id": SID, "cwd": str(REPO), "hook_event_name": "SessionStart"}))
        p.stdin.close()
        self.addCleanup(lambda: p.poll() is None and p.kill())
        return p

    def wait_for(self, cond, secs=10):
        end = time.time() + secs
        while time.time() < end:
            if cond():
                return True
            time.sleep(0.1)
        return False


class TestListener(LinkCase):
    def test_a_session_registers_and_a_message_wakes_it_with_exit_2(self):
        p = self.listener()
        self.assertTrue(self.wait_for(lambda: any(s["id"] == SID for s in link.sessions())))
        s = link.sessions()[0]
        self.assertEqual((s["project"], s["state"]), (REPO.name, "listening"))
        link.send(SID, "Run the tests", "user")
        self.assertEqual(p.wait(10), 2)
        err = p.stderr.read()
        self.assertIn("Run the tests", err)
        self.assertIn("sent by the user", err)
        self.assertEqual(link.delivered(SID)[0]["text"], "Run the tests")
        self.assertEqual(link.sessions()[0]["state"], "busy")  # until the next Stop starts a listener

    def test_only_one_listener_per_session_and_session_end_unregisters(self):
        first = self.listener()
        self.assertTrue(self.wait_for(lambda: (self.home / "sessions" / f"{SID}.pid").exists()))
        second = self.listener()
        self.assertEqual(second.wait(10), 0)
        end = self.listener("end")
        self.assertEqual(end.wait(10), 0)
        self.assertTrue(self.wait_for(lambda: first.poll() is not None))
        self.assertEqual(link.sessions(), [])

    def test_a_bad_session_id_is_ignored(self):
        p = subprocess.run([sys.executable, str(LISTEN)], input=json.dumps({"session_id": "../x"}), capture_output=True,
                           text=True, env=self.env, timeout=10)
        self.assertEqual(p.returncode, 0)
        self.assertFalse((self.home / "sessions").exists())


class TestTankaSide(LinkCase):
    def register(self, seen_ago=0):
        (self.home / "sessions").mkdir(parents=True, exist_ok=True)
        (self.home / "sessions" / f"{SID}.json").write_text(json.dumps(
            {"id": SID, "cwd": "/code/app", "project": "app", "branch": "main", "seen": time.time() - seen_ago}))

    def test_old_sessions_without_a_listener_are_not_listed_and_cannot_be_sent_to(self):
        self.register(seen_ago=link.BUSY_FOR + 10)
        self.assertEqual(link.sessions(), [])
        with self.assertRaisesRegex(link.ToolError, "not open"):
            link.send(SID, "hi", "user")

    def test_the_assistant_lists_by_number_and_sends_with_its_name(self):
        self.register()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            link.sessions_tool("assistant")
            link.send_tool("assistant", 1, "Summarize the diff", "Clara")
        self.assertIn("1 | app (main) | busy", out.getvalue())
        msg = json.loads(next((self.home / "inbox" / SID).glob("*.json")).read_text())
        self.assertEqual((msg["from"], msg["text"]), ("Clara", "Summarize the diff"))
        with self.assertRaisesRegex(link.ToolError, "no session number 2"):
            link.send_tool("assistant", 2, "x", "Clara")

    def test_empty_or_huge_messages_are_refused(self):
        self.register()
        for text in ("", "x" * (link.TEXT_MAX + 1)):
            with self.assertRaises(link.ToolError):
                link.send(SID, text, "user")


if __name__ == "__main__":
    unittest.main()
