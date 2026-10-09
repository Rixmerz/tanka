"""The page's Autonomy switches: each module offers its own, and only the user's click flips them."""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))


def page_of(module: str):
    sys.path.insert(0, str(REPO / "modules" / module))
    spec = importlib.util.spec_from_file_location(f"autonomy_page_{module}", REPO / "modules" / module / "page.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class AutonomyCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def patch(self, obj, name, value):
        old = getattr(obj, name)
        setattr(obj, name, value)
        self.addCleanup(setattr, obj, name, old)


class TestJira(AutonomyCase):
    def test_the_switch_exists_only_where_writing_is_allowed_and_flips_that_entry(self):
        page = page_of("jira")
        scopes = self.tmp / "scopes.json"
        scopes.write_text(json.dumps({"scopes": {"assistant": {"projects": ["PROJ"], "write": True},
                                                 "reader": {"projects": ["PROJ"], "write": False}}}))
        self.patch(page.j, "SCOPES", scopes)
        self.patch(page.j, "DELETIONS", self.tmp / "deletions")
        self.assertEqual(page.state("reader", self.tmp)["autonomy"], [])
        self.assertFalse(page.state("assistant", self.tmp)["autonomy"][0]["on"])
        page.ACTIONS["autonomy"]("assistant", self.tmp, {"key": "unattended_write", "on": True})
        data = json.loads(scopes.read_text())["scopes"]
        self.assertEqual(data["assistant"], {"projects": ["PROJ"], "write": True, "unattended_write": True})
        with self.assertRaises(Exception):
            page.ACTIONS["autonomy"]("reader", self.tmp, {"key": "unattended_write", "on": True})


class TestCalendar(AutonomyCase):
    def test_the_switch_covers_only_this_workspaces_accounts(self):
        page = page_of("calendar")
        reg = self.tmp / "accounts.json"
        reg.write_text(json.dumps({"accounts": {"me@example.com": {"workspace": "assistant"},
                                                "other@example.com": {"workspace": "work"}}}))
        self.patch(page.g, "REGISTRY", reg)
        self.patch(page.g, "REQUESTS", self.tmp / "requests")
        self.assertFalse(page.state("assistant", self.tmp)["autonomy"][0]["on"])
        page.ACTIONS["autonomy"]("assistant", self.tmp, {"key": "unattended_write", "on": True})
        acc = json.loads(reg.read_text())["accounts"]
        self.assertTrue(acc["me@example.com"]["unattended_write"])
        self.assertNotIn("unattended_write", acc["other@example.com"])
        self.assertTrue(page.state("assistant", self.tmp)["autonomy"][0]["on"])


if __name__ == "__main__":
    unittest.main()
