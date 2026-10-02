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


class TestNeeds(ModulesCase):
    def needs(self, name, needs):
        mj = tm.MODULES / name / "module.json"
        mj.write_text(json.dumps(dict(json.loads(mj.read_text()), needs=needs)))

    def test_a_need_must_be_another_module(self):
        self.make("top")
        self.needs("top", ["nowhere"])
        self.assertTrue(any("needs names 'nowhere'" in p for p in tm.check("top")))

    def test_what_it_needs_comes_first_and_counts_toward_the_budget(self):
        self.make("top", tools=2)
        self.needs("top", ["demo"])
        self.assertEqual(tm.install("top", self.ws, "shop"), 0)
        self.assertEqual(sorted(p.name for p in tt.skills_dir(self.ws).iterdir()), ["demo", "top"])
        self.make("huge", tools=5)
        self.make("dep", tools=5)
        self.needs("huge", ["dep"])
        for i in range(2):
            self.make(f"pad{i}", tools=2)
            tm.install(f"pad{i}", self.ws, "shop")
        # 3 + 4 tools installed; huge (5) plus dep (5) would go past 15, so neither goes in
        self.assertEqual(tm.install("huge", self.ws, "shop"), 1)
        self.assertFalse((tt.skills_dir(self.ws) / "dep").exists())


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


class TestMarker(ModulesCase):
    def test_install_marks_the_copy_and_the_checks_ignore_the_mark(self):
        self.assertEqual(tm.install("demo", self.ws, "shop"), 0)
        marker = json.loads((tt.skills_dir(self.ws) / "demo" / tm.MARKER).read_text())
        self.assertEqual(marker["module"], "demo")
        self.assertIsInstance(marker["installed"], int)
        tools, problems = tt.scan(self.ws)
        self.assertEqual((list(tools), problems, tt.skill_warnings(self.ws)), (["demo_tool0"], [], []))
        self.assertEqual(tm.check("demo"), [])
        self.assertEqual(tm.module_of(tt.skills_dir(self.ws) / "demo"), "demo")

    def test_a_copy_without_the_mark_counts_when_it_has_every_shipped_tool(self):
        tm.copy_skill(tm.load("demo"), self.ws, "shop")
        sdir = tt.skills_dir(self.ws) / "demo"
        self.assertEqual(tm.module_of(sdir), "demo")
        (sdir / "tools" / "demo_extra.json").write_text("{}")  # more than it ships (boards adds one per view): still the module's
        self.assertEqual(tm.module_of(sdir), "demo")
        (sdir / "tools" / "demo_tool0.json").unlink()
        self.assertIsNone(tm.module_of(sdir))


class TestUninstall(ModulesCase):
    def needs(self, name, needs):
        mj = tm.MODULES / name / "module.json"
        mj.write_text(json.dumps(dict(json.loads(mj.read_text()), needs=needs)))

    def test_removes_the_skill_and_nothing_else(self):
        tm.install("demo", self.ws, "shop")
        (self.ws / ".claude" / "demo.json").write_text("{}")
        self.assertEqual(tm.uninstall("demo", self.ws, "shop"), 0)
        self.assertFalse((tt.skills_dir(self.ws) / "demo").exists())
        self.assertTrue((self.ws / ".claude" / "demo.json").is_file())
        self.assertEqual(tm.uninstall("demo", self.ws, "shop"), 1)  # not installed any more
        self.assertEqual(tm.uninstall("nope", self.ws, "shop"), 1)

    def test_refuses_a_module_another_one_needs(self):
        self.make("top")
        self.needs("top", ["demo"])
        tm.install("top", self.ws, "shop")
        self.assertEqual(tm.uninstall("demo", self.ws, "shop"), 1)
        self.assertTrue((tt.skills_dir(self.ws) / "demo").is_dir())
        self.assertEqual(tm.uninstall("top", self.ws, "shop"), 0)
        self.assertEqual(tm.uninstall("demo", self.ws, "shop"), 0)

    def test_refuses_a_skill_of_the_workspace_own(self):
        own = tt.skills_dir(self.ws) / "demo"
        own.mkdir()
        (own / "SKILL.md").write_text("---\nname: demo\ndescription: mine.\n---\n")
        self.assertEqual(tm.uninstall("demo", self.ws, "shop"), 1)
        self.assertTrue(own.is_dir())

    def test_runs_post_remove_when_the_module_has_one(self):
        tm.install("demo", self.ws, "shop")
        out = self.tmp / "removed.txt"
        (tm.MODULES / "demo" / "cli.py").write_text(
            "import sys, pathlib\n"
            "if sys.argv[1] == 'post-remove':\n"
            f"    pathlib.Path({str(out)!r}).write_text(sys.argv[3])\n")
        self.assertEqual(tm.uninstall("demo", self.ws, "shop"), 0)
        self.assertEqual(out.read_text(), "shop")

    def test_skills_turn_off_and_on_within_the_budget(self):
        tm.install("demo", self.ws, "shop")
        own = tt.skills_dir(self.ws) / "notes"
        (own / "tools").mkdir(parents=True)
        (own / "SKILL.md").write_text("---\nname: notes\ndescription: mine.\n---\n")
        tm.set_enabled(self.ws, "notes", False)
        self.assertTrue((tm.off_dir(self.ws) / "notes").is_dir())
        self.assertEqual([(s["name"], s["enabled"], s["origin"]) for s in tm.skills(self.ws)],
                         [("demo", True, "module"), ("notes", False, "own")])
        tm.set_enabled(self.ws, "notes", True)
        self.assertTrue(own.is_dir())
        with self.assertRaises(ValueError):
            tm.set_enabled(self.ws, "demo", False)
        with self.assertRaises(ValueError):
            tm.set_enabled(self.ws, "../demo", False)


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
        p = subprocess.run([str(REPO / "bin" / "tanka"), "init", "uninstall"], capture_output=True, text=True, env=env)
        self.assertEqual(p.returncode, 1)

    def test_uninstall_needs_a_module_and_a_workspace(self):
        p = subprocess.run([str(REPO / "bin" / "tanka"), "uninstall", "desk"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("Usage: tanka uninstall <module> <workspace>", p.stderr)


if __name__ == "__main__":
    unittest.main()
