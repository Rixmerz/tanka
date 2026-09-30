"""Tanka modules: listing, validation and installation into a workspace."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import tanka_modules as tm  # noqa: E402
import tanka_tools as tt  # noqa: E402


class ModulesCase(unittest.TestCase):
    """A fake modules/ directory with one good module, so the tests do not depend on the shipped ones."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        old = tm.MODULES
        tm.MODULES = self.tmp / "modules"
        self.addCleanup(setattr, tm, "MODULES", old)
        self.make("demo")
        self.ws = self.tmp / "ws"
        tt.skills_dir(self.ws).mkdir(parents=True)

    def make(self, name: str, tools: int = 1, scoped: bool = True) -> Path:
        d = tm.MODULES / name
        (d / "skill" / "tools").mkdir(parents=True)
        (d / "module.json").write_text(json.dumps({"name": name, "description": "A test module.", "requires": []}))
        (d / "README.md").write_text("# demo\n")
        names = [f"{name}_tool{i}" for i in range(tools)]
        (d / "skill" / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Test module used by the test suite only.\n---\n" + " ".join(names) + "\n")
        for n in names:
            (d / "skill" / "tools" / f"{n}.py").write_text('SCOPE = "__SCOPE__"\n' if scoped else "print(1)\n")
            (d / "skill" / "tools" / f"{n}.json").write_text(json.dumps({
                "name": n, "effect": "read",
                "description": "Returns a line for the test suite. Use it only in tests of the module installer. "
                               "It does nothing else, and no other tool in the workspace does this. Returns one line of text.",
                "params": {}, "examples": [{}], "run": ["python3", f"{n}.py"]}))
        return d


class TestCheck(ModulesCase):
    def test_good_module(self):
        self.assertEqual(tm.check("demo"), [])

    def test_unknown_module(self):
        self.assertIn("no module named 'nope'", tm.check("nope")[0])

    def test_unscoped_tools_are_refused(self):
        self.make("loose", scoped=False)
        self.assertTrue(any("__SCOPE__" in p for p in tm.check("loose")))


class TestInstall(ModulesCase):
    def test_install_replaces_the_scope(self):
        self.assertEqual(tm.install("demo", self.ws, "shop"), 0)
        script = tt.skills_dir(self.ws) / "demo" / "tools" / "demo_tool0.py"
        self.assertEqual(script.read_text(), 'SCOPE = "shop"\n')
        self.assertEqual(len(tt.scan(self.ws)[0]), 1)

    def test_never_overwrites(self):
        tm.install("demo", self.ws, "shop")
        self.assertEqual(tm.install("demo", self.ws, "shop"), 1)

    def test_refuses_to_go_over_the_budget(self):
        for i in range(3):
            self.make(f"big{i}", tools=5)
        for i in range(3):
            self.assertEqual(tm.install(f"big{i}", self.ws, "x"), 0)
        self.assertEqual(tm.install("demo", self.ws, "x"), 1)
        self.assertFalse((tt.skills_dir(self.ws) / "demo").exists())

    def test_bad_scope(self):
        self.assertEqual(tm.install("demo", self.ws, "../evil"), 1)


class TestLauncher(unittest.TestCase):
    def test_modules_list_and_shipped_modules_are_valid(self):
        p = subprocess.run([str(REPO / "bin" / "tanka"), "modules"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0)
        for m in tm.available():
            with self.subTest(module=m["name"]):
                self.assertIn(m["name"], p.stdout)
                self.assertEqual(tm.check(m["name"]), [])

    def test_module_name_is_not_a_workspace_name(self):
        env = dict(os.environ, TANKA_WORKSPACES=tempfile.mkdtemp())
        p = subprocess.run([str(REPO / "bin" / "tanka"), "init", "whatsapp"], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 1)
        self.assertIn("is a tanka command or module", p.stderr)


if __name__ == "__main__":
    unittest.main()
