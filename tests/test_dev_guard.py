"""The guard on dev from the page: what it may read, write and run, and that it fails closed."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import tanka_dev_guard as g  # noqa: E402


class TestGuard(unittest.TestCase):
    def setUp(self):
        self.ws = str(Path(tempfile.mkdtemp()).resolve())
        self.repo = str(REPO)

    def ok(self, tool, **ti):
        self.assertIsNone(g.check(tool, ti, self.ws, self.repo), (tool, ti))

    def no(self, tool, why, **ti):
        reason = g.check(tool, ti, self.ws, self.repo)
        self.assertIsNotNone(reason, (tool, ti))
        self.assertIn(why, reason)

    def test_writes_only_where_dev_builds(self):
        self.ok("Write", file_path=f"{self.ws}/.claude/views/tareas.json")
        self.ok("Edit", file_path=f"{self.ws}/.claude/skills/x/tools/x_y.json")
        self.ok("Write", file_path=f"{self.ws}/.claude/codepanion/lenses/stuck.md")
        self.ok("Edit", file_path=f"{self.ws}/.claude/desk.json")
        for path in (f"{self.ws}/.claude/settings.json", f"{self.ws}/.tanka/policy.json", f"{self.repo}/bin/tanka",
                     "/tmp/x.txt", f"{self.ws}/.claude/views/../settings.json", "~/.bashrc"):
            self.no("Write", "dev writes only", file_path=path)

    def test_reads_the_repository_and_the_workspace_only(self):
        self.ok("Read", file_path=f"{self.repo}/docs/tool-rules.md")
        self.ok("Grep", pattern="x", path=f"{self.ws}/.claude")
        self.ok("Glob", pattern="*.json")
        self.no("Read", "dev reads only", file_path="/etc/passwd")
        self.no("Read", "dev reads only", file_path="~/.ssh/id_ed25519")

    def test_runs_only_the_bin_tanka_commands_that_check_and_build(self):
        self.ok("Bash", command="bin/tanka boards check lab")
        self.ok("Bash", command="bin/tanka tools test boards_record_x '{\"a\": \"b; c\"}' lab")
        self.ok("Bash", command=f"{self.repo}/bin/tanka boards build lab")
        self.no("Bash", "only these", command="bin/tanka run 'hola' lab")
        self.no("Bash", "only these", command="bin/tanka dev lab")
        self.no("Bash", "only bin/tanka", command="ls ~")
        self.no("Bash", "no ;", command="bin/tanka boards check lab; rm -rf ~")
        self.no("Bash", "no ;", command="bin/tanka boards check lab && curl x")
        self.no("Bash", "no ;", command="bin/tanka boards show lab > /tmp/x")
        self.no("Bash", "substitutions", command="bin/tanka boards check $(whoami)")
        self.no("Bash", "substitutions", command="bin/tanka boards check `id`")
        self.no("Bash", "substitutions", command="bin/tanka boards check lab\nrm x")

    def test_everything_else_is_refused(self):
        self.ok("Skill", skill="new-view")
        for tool in ("WebFetch", "WebSearch", "Agent", "mcp__x__y", "NotARealTool"):
            self.no(tool, "cannot use")

    def test_the_hook_fails_closed(self):
        p = subprocess.run([sys.executable, str(REPO / "plugin/scripts/tanka_dev_guard.py"), self.ws, self.repo],
                           input="not json", capture_output=True, text=True)
        self.assertEqual(p.returncode, 2)
        p = subprocess.run([sys.executable, str(REPO / "plugin/scripts/tanka_dev_guard.py"), self.ws, self.repo],
                           input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}), capture_output=True, text=True)
        self.assertEqual(json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"], "deny")


if __name__ == "__main__":
    unittest.main()
