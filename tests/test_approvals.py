"""Approvals: a tool only asks, and only the user's click runs the skill's approve.py, once."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
sys.path.insert(0, str(REPO / "modules" / "approvals"))
import approvals as ap  # noqa: E402

_spec = importlib.util.spec_from_file_location("approvals_page", REPO / "modules" / "approvals" / "page.py")
approvals_page = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(approvals_page)

APPROVE_OK = """import json, sys
from pathlib import Path
d = json.load(sys.stdin)
with open(Path(__file__).with_name("ran.log"), "a") as f:
    f.write(json.dumps(d["payload"]) + "\\n")
print("Created EXAMPLE-1")
"""


class ApprovalsCase(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="tanka-approvals-"))
        old = ap.HOME
        ap.HOME = self.home / "shared"
        self.addCleanup(setattr, ap, "HOME", old)
        self.ws = self.home / "ws"
        self.skill = self.ws / ".claude" / "skills" / "ticket"
        self.skill.mkdir(parents=True)

    def approve_script(self, body: str) -> None:
        (self.skill / "approve.py").write_text(body, encoding="utf-8")

    def ran(self) -> list[dict]:
        log = self.skill / "ran.log"
        return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []

    def test_ask_runs_nothing_and_refuses_bad_scope_or_duplicate(self):
        self.approve_script(APPROVE_OK)
        a = ap.ask("ws", "ticket", "Open a ticket", "Hello,\n\nPlease.", {"title": "Open a ticket"})
        self.assertEqual(a["status"], "pending")
        self.assertEqual(self.ran(), [])
        with self.assertRaises(ap.ToolError):
            ap.ask("ws", "ticket", "Open a ticket", "again", {})
        with self.assertRaises(ap.ToolError):
            ap.ask("../x", "ticket", "t", "x", {})
        with self.assertRaises(ap.ToolError):
            ap.ask("ws", "../ticket", "t", "x", {})

    def test_approve_runs_once_with_the_stored_payload(self):
        self.approve_script(APPROVE_OK)
        a = ap.ask("ws", "ticket", "Open a ticket", "text", {"title": "Open a ticket", "n": 1})
        self.assertEqual(ap.approve("ws", self.ws, a["id"]), "Created EXAMPLE-1")
        self.assertEqual(self.ran(), [{"title": "Open a ticket", "n": 1}])
        with self.assertRaises(ap.ToolError):
            ap.approve("ws", self.ws, a["id"])
        self.assertEqual(len(self.ran()), 1)
        closed = ap.read("ws")[0]
        self.assertEqual((closed["status"], closed["result"]), ("approved", "Created EXAMPLE-1"))

    def test_failure_is_kept_and_not_retried(self):
        self.approve_script("import sys\nsys.exit('the service said no')\n")
        a = ap.ask("ws", "ticket", "Open a ticket", "text", {})
        with self.assertRaises(ap.ToolError) as cm:
            ap.approve("ws", self.ws, a["id"])
        self.assertIn("the service said no", str(cm.exception))
        self.assertEqual(ap.read("ws")[0]["status"], "failed")
        with self.assertRaises(ap.ToolError):
            ap.approve("ws", self.ws, a["id"])

    def test_dismiss_and_missing_approve_script(self):
        a = ap.ask("ws", "ticket", "Open a ticket", "text", {})
        with self.assertRaises(ap.ToolError):
            ap.approve("ws", self.ws, a["id"])
        self.assertEqual(ap.read("ws")[0]["status"], "pending")
        self.assertIn("nothing was done", ap.dismiss("ws", a["id"]))
        self.assertEqual(ap.read("ws")[0]["status"], "dismissed")
        with self.assertRaises(ap.ToolError):
            ap.dismiss("ws", a["id"])

    def test_page_shows_the_request_under_its_answer(self):
        t0 = time.time()
        a = ap.ask("ws", "ticket", "Open a ticket", "text", {})
        (fx,), (none,) = approvals_page.effects("ws", self.ws, [(t0 - 1, time.time() + 1)]), \
            approvals_page.effects("ws", self.ws, [(0, t0 - 1)])
        self.assertEqual([(f["type"], f["id"], f["status"]) for f in fx], [("approval", a["id"], "pending")])
        self.assertEqual(none, [])
        self.assertNotIn("payload", approvals_page.state("ws", self.ws)["approvals"][0])


if __name__ == "__main__":
    unittest.main()
