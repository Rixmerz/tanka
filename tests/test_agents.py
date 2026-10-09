"""Tanka subagents: the agent block, its prompt file, the confined command and the runner.

No API calls: a fake `claude` on PATH answers like `claude -p --output-format json`.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tanka_agent as ta  # noqa: E402
import tanka_tools as tt  # noqa: E402
from test_hooks import TEMPLATE  # noqa: E402

PROMPT = "Read the only .txt file in the current folder about {topic} and return its due date and a one-line summary."
FAKE_CLAUDE = """#!/usr/bin/env python3
import json, os, sys
if os.environ.get("FAKE_CLAUDE_ARGV"):
    open(os.environ["FAKE_CLAUDE_ARGV"], "w").write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "env": sorted(os.environ)}))
print(json.dumps({"subtype": "success", "is_error": False, "result": "", "total_cost_usd": 0.01,
                  "structured_output": {"date": "2026-11-30", "summary": "due soon"}}))
"""


def agent_manifest(**over) -> dict:
    m = {
        "name": "demo_summarise",
        "effect": "read",
        "description": ("Summarises one document with a stronger model and returns its due date. Use it when the user asks "
                        "what a document says. Do not use it to send anything. It returns the date and a one-line summary."),
        "params": {"topic": {"type": "string", "required": True, "description": "What the document is about (e.g. rent)."}},
        "examples": [{"topic": "rent"}],
        "agent": {"model": "opus", "effort": "low", "tools": ["Read", "Glob"], "max_budget_usd": 1,
                  "output": {"type": "object", "properties": {"date": {"type": "string"}, "summary": {"type": "string"}}}},
        "timeout_sec": 120,
    }
    m.update(over)
    return m


class AgentValidationCase(unittest.TestCase):
    def errs(self, **agent_over) -> list[str]:
        m = agent_manifest()
        m["agent"] = {**m["agent"], **agent_over}
        return tt.validate_manifest(m, "demo", "demo_summarise")

    def test_valid_agent_manifest(self):
        self.assertEqual(tt.validate_manifest(agent_manifest(), "demo", "demo_summarise"), [])

    def test_model_effort_tools_budget_are_closed_sets(self):
        self.assertTrue(any("agent.model" in e for e in self.errs(model="gpt")))
        self.assertTrue(any("agent.effort" in e for e in self.errs(effort="huge")))
        self.assertTrue(any("agent.tools" in e for e in self.errs(tools=["Write"])))
        self.assertTrue(any("max_budget_usd" in e for e in self.errs(max_budget_usd=0)))
        self.assertTrue(any("max_budget_usd" in e for e in self.errs(max_budget_usd=500)))

    def test_subagent_never_publishes(self):
        for effect in ("send", "modify"):
            errs = tt.validate_manifest(agent_manifest(effect=effect), "demo", "demo_summarise")
            self.assertTrue(any("proposes" in e for e in errs), errs)

    def test_run_and_agent_are_exclusive(self):
        errs = tt.validate_manifest(agent_manifest(run=["python3", "x.py"]), "demo", "demo_summarise")
        self.assertTrue(any("not both" in e for e in errs))

    def test_workdir_needs_a_pinned_path_param(self):
        m = agent_manifest()
        m["agent"]["workdir"] = "{topic}"
        self.assertTrue(any("workdir" in e for e in tt.validate_manifest(m, "demo", "demo_summarise")))
        m["params"]["topic"]["pattern"] = "/home/[^/]+/docs/.+"
        m["examples"] = [{"topic": "/home/ana/docs/rent"}]
        self.assertEqual(tt.validate_manifest(m, "demo", "demo_summarise"), [])

    def test_output_must_be_an_object_schema(self):
        self.assertTrue(any("agent.output" in e for e in self.errs(output={"type": "string"})))


class AgentCommandCase(unittest.TestCase):
    def test_bash_always_runs_in_a_fail_closed_sandbox(self):
        cmd = ta.command("opus", "low", ["Read", "Bash"], "p", 1.0)
        sandbox = json.loads(cmd[cmd.index("--settings") + 1])["sandbox"]
        self.assertTrue(sandbox["enabled"] and sandbox["failIfUnavailable"])
        self.assertTrue(sandbox["network"]["strictAllowlist"])
        self.assertIn(str(Path.home() / ".ssh"), sandbox["filesystem"]["denyRead"])

    def test_no_bash_no_sandbox_and_always_restricted(self):
        cmd = ta.command("haiku", "low", ["Read"], "p", 0.5, schema={"type": "object"})
        self.assertNotIn("--settings", cmd)
        for flag in ("--restricted", "--strict-mcp-config", "--no-session-persistence", "--json-schema"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("--permission-prompts") + 1], "none")
        self.assertEqual(cmd[cmd.index("--model") + 1], "claude-haiku-5-5")

    def test_child_does_not_inherit_the_session(self):
        keep = dict(os.environ)
        try:
            os.environ.update(TANKA_WORKSPACE="/w", CLAUDECODE="1", CLAUDE_CODE_SESSION_ID="s", ANTHROPIC_MODEL="haiku")
            env = ta.clean_env()
        finally:
            os.environ.clear()
            os.environ.update(keep)
        for k in ("TANKA_WORKSPACE", "CLAUDECODE", "CLAUDE_CODE_SESSION_ID", "ANTHROPIC_MODEL"):
            self.assertNotIn(k, env)
        self.assertIn("PATH", env)

    def test_parse(self):
        ok = ta.parse(json.dumps({"subtype": "success", "structured_output": {"a": 1}, "total_cost_usd": 0.2}), "", 0)
        self.assertEqual((ok["ok"], ok["data"]), (True, {"a": 1}))
        bad = ta.parse(json.dumps({"subtype": "error_max_budget_usd", "is_error": True, "result": ""}), "", 1)
        self.assertFalse(bad["ok"])
        self.assertIn("max_budget", bad["error"])
        nosb = ta.parse("", "Warning: no stdin\nError: sandbox required but unavailable: socat not installed", 2)
        self.assertIn("sandbox is not available", nosb["error"])


class AgentRunCase(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="tanka-agents-"))
        shutil.copytree(TEMPLATE, self.ws, dirs_exist_ok=True)
        self.tools = self.ws / ".claude" / "skills" / "demo" / "tools"
        self.tools.mkdir(parents=True)
        (self.tools.parent / "SKILL.md").write_text("---\nname: demo\ndescription: d\n---\nUse demo_summarise.\n", encoding="utf-8")
        self.bin = self.ws / "bin"
        self.bin.mkdir()
        fake = self.bin / "claude"
        fake.write_text(FAKE_CLAUDE, encoding="utf-8")
        fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
        self.argv_file = self.ws / "argv.json"
        self.old_env = dict(os.environ)
        os.environ["PATH"] = f"{self.bin}{os.pathsep}{os.environ['PATH']}"
        os.environ["FAKE_CLAUDE_ARGV"] = str(self.argv_file)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.old_env)
        shutil.rmtree(self.ws, ignore_errors=True)

    def add(self, m: dict, prompt: str | None = PROMPT):
        (self.tools / f"{m['name']}.json").write_text(json.dumps(m), encoding="utf-8")
        if prompt is not None:
            (self.tools / f"{m['name']}.md").write_text(prompt, encoding="utf-8")

    def test_prompt_file_is_required_and_its_placeholders_checked(self):
        self.add(agent_manifest(), prompt=None)
        tools, problems = tt.scan(self.ws)
        self.assertNotIn("demo_summarise", tools)
        self.assertTrue(any("tools/demo_summarise.md" in p for p in problems))
        self.add(agent_manifest(), prompt=PROMPT + " Also mention {owner}.")
        _, problems = tt.scan(self.ws)
        self.assertTrue(any("{owner}" in p for p in problems))

    def test_foreground_call_renders_prompt_and_returns_structured_result(self):
        self.add(agent_manifest())
        tools, _ = tt.scan(self.ws)
        text, err = tt.run_tool(self.ws, tools["demo_summarise"], {"topic": "rent"})
        self.assertFalse(err, text)
        self.assertIn('"date": "2026-11-30"', text)
        self.assertIn("opus, low, USD 0.01", text)
        seen = json.loads(self.argv_file.read_text())
        self.assertIn("about rent", seen["argv"][seen["argv"].index("-p") + 1])
        self.assertNotIn("TANKA_WORKSPACE", seen["env"])
        self.assertTrue(seen["cwd"].endswith(".tanka/agents/demo_summarise/work"))

    def test_background_call_starts_then_returns_the_result(self):
        m = agent_manifest()
        m["agent"]["background"] = True
        self.add(m)
        tools, _ = tt.scan(self.ws)
        text, err = tt.run_tool(self.ws, tools["demo_summarise"], {"topic": "rent"})
        self.assertFalse(err)
        self.assertIn("started in the background", text)
        for _ in range(50):
            text, err = tt.run_tool(self.ws, tools["demo_summarise"], {"topic": "rent"})
            if "answered" in text:
                break
            time.sleep(0.1)
        self.assertIn('"summary": "due soon"', text)


if __name__ == "__main__":
    unittest.main()
