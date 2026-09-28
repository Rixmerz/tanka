"""Tanka tools: manifest rules, execution, the MCP server and the hook policy."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tanka_tools as tt  # noqa: E402
from test_hooks import TEMPLATE, decision, reason, run_hook  # noqa: E402

ECHO = "import json,sys; a=json.load(sys.stdin); print('args', json.dumps(a, sort_keys=True), 'argv', sys.argv[1:])"


def manifest(**over) -> dict:
    m = {
        "name": "demo_echo",
        "effect": "read",
        "description": ("Echoes its arguments back so the tests can see them. Use it only in tests. "
                        "Do not use it for anything a user asks. It returns the arguments and the argv it received."),
        "params": {
            "course_id": {"type": "integer", "required": True, "description": "Numeric id of the course."},
            "text": {"type": "string", "description": "Optional free text to pass along."},
        },
        "examples": [{"course_id": 1}],
        "run": ["python3", "echo.py", "--id={course_id}", ["--text", "{text}"]],
        "timeout_sec": 10,
    }
    m.update(over)
    return m


class ToolsCase(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="tanka-tools-"))
        shutil.copytree(TEMPLATE, self.ws, dirs_exist_ok=True)
        self.skill = self.ws / ".claude" / "skills" / "demo"
        (self.skill / "tools").mkdir(parents=True)
        (self.skill / "tools" / "echo.py").write_text(ECHO, encoding="utf-8")
        self.write_skill("Use demo_echo to echo.")

    def tearDown(self):
        shutil.rmtree(self.ws, ignore_errors=True)

    def write_skill(self, body: str):
        (self.skill / "SKILL.md").write_text(f"---\nname: demo\ndescription: demo\n---\n{body}\n", encoding="utf-8")

    def add(self, m: dict, skill_dir: Path | None = None):
        d = (skill_dir or self.skill) / "tools"
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{m['name']}.json").write_text(json.dumps(m), encoding="utf-8")


class TestManifestRules(ToolsCase):
    def errs(self, m, skill="demo", stem=None):
        return " | ".join(tt.validate_manifest(m, skill, stem or m.get("name", "")))

    def test_valid_manifest_passes(self):
        self.assertEqual(tt.validate_manifest(manifest(), "demo", "demo_echo"), [])

    def test_prefix_comes_from_skill_dir(self):
        self.assertIn("skill prefix 'demo_'", self.errs(manifest(name="other_echo")))
        self.assertEqual(tt.validate_manifest(manifest(name="win10_vm_run"), "win10-vm", "win10_vm_run"), [])

    def test_destructive_is_never_a_tool(self):
        self.assertIn("destructive", self.errs(manifest(effect="destructive")))

    def test_param_limits(self):
        many = {f"p{i}": {"type": "string", "description": "a parameter here"} for i in range(tt.MAX_PARAMS + 1)}
        self.assertIn(f"at most {tt.MAX_PARAMS} params", self.errs(manifest(params=many, examples=[{}], run=["python3", "echo.py"])))
        req = {f"p{i}": {"type": "string", "required": True, "description": "a parameter here"} for i in range(tt.MAX_REQUIRED + 1)}
        self.assertIn(f"at most {tt.MAX_REQUIRED} required", self.errs(manifest(params=req, run=["python3", "echo.py"])))

    def test_no_nested_types(self):
        p = {"items": {"type": "array", "description": "a list of things"}}
        self.assertIn("type must be one of", self.errs(manifest(params=p, examples=[{}], run=["python3", "echo.py"])))

    def test_description_needs_three_sentences(self):
        self.assertIn("sentences", self.errs(manifest(description="Echoes things back to the caller for the test suite, with a long enough text to pass the length rule but a single sentence only")))

    def test_examples_required_and_checked(self):
        self.assertIn("examples", self.errs(manifest(examples=[])))
        self.assertIn("example 1", self.errs(manifest(examples=[{"course_id": "x"}])))

    def test_optional_placeholder_must_be_grouped(self):
        self.assertIn("wrap it", self.errs(manifest(run=["python3", "echo.py", "{text}"])))

    def test_placeholder_must_exist(self):
        self.assertIn("{nope}", self.errs(manifest(run=["python3", "echo.py", "{nope}"])))

    def test_file_name_must_match(self):
        self.assertIn("demo_echo.json", self.errs(manifest(), stem="other"))

    def test_skill_must_mention_tool(self):
        self.write_skill("No tools named here.")
        self.add(manifest())
        tools, problems = tt.scan(self.ws)
        self.assertEqual(tools, {})
        self.assertIn("never mentions demo_echo", " ".join(problems))

    def test_per_skill_limit_loads_none(self):
        names = [f"demo_tool{i}" for i in range(tt.MAX_TOOLS_PER_SKILL + 1)]
        self.write_skill(" ".join(names))
        for n in names:
            self.add(manifest(name=n))
        tools, problems = tt.scan(self.ws)
        self.assertEqual(tools, {})
        self.assertIn("none of them were loaded", " ".join(problems))

    def test_total_limit(self):
        for s in range(3):
            sd = self.ws / ".claude" / "skills" / f"s{s}"
            names = [f"s{s}_tool{i}" for i in range(6)]
            (sd / "tools").mkdir(parents=True)
            (sd / "SKILL.md").write_text(" ".join(names), encoding="utf-8")
            for n in names:
                self.add(manifest(name=n), sd)
        tools, problems = tt.scan(self.ws)
        self.assertEqual(len(tools), tt.MAX_TOOLS_TOTAL)
        self.assertIn("limit is 15", " ".join(problems))


class TestSkillRules(ToolsCase):
    def test_clean_skill_has_no_warnings(self):
        self.assertEqual(tt.skill_warnings(self.ws), [])

    def test_warnings(self):
        (self.skill / "SKILL.md").write_text("---\nname: other\n---\n```bash\nrm x\n```\n" + "line\n" * tt.MAX_SKILL_LINES, encoding="utf-8")
        w = " | ".join(tt.skill_warnings(self.ws))
        for part in ("name must be 'demo'", "needs a description", "keep it under", "shell code blocks"):
            self.assertIn(part, w)

    def test_skills_line_lists_tools(self):
        import tanka_common as tc
        self.add(manifest())
        line = tc.skills_line(self.ws)
        self.assertIn("demo (demo_echo)", line)
        self.assertIn("never work around", line)


class TestExecution(ToolsCase):
    def call(self, args, **over):
        self.add(manifest(**over))
        tools, problems = tt.scan(self.ws)
        self.assertEqual(problems, [])
        return tt.run_tool(self.ws, tools["demo_echo"], args)

    def test_argv_and_stdin(self):
        text, err = self.call({"course_id": 7, "text": "hello world"})
        self.assertFalse(err, text)
        self.assertIn('"course_id": 7', text)
        self.assertIn("'--id=7', '--text', 'hello world'", text)

    def test_optional_group_dropped(self):
        text, err = self.call({"course_id": 7})
        self.assertFalse(err, text)
        self.assertIn("argv ['--id=7']", text)

    def test_leading_dash_rejected_as_whole_arg(self):
        text, err = self.call({"course_id": 7, "text": "--rm"})
        self.assertTrue(err)
        self.assertIn("cannot start with '-'", text)

    def test_bad_args_explain_fix(self):
        text, err = self.call({"course_id": "7", "extra": 1})
        self.assertTrue(err)
        self.assertIn("unknown field(s): extra", text)
        self.assertIn("whole number", text)

    def test_failure_is_error_with_stderr(self):
        (self.skill / "tools" / "echo.py").write_text("import sys; sys.exit('boom: fix the input')", encoding="utf-8")
        text, err = self.call({"course_id": 1})
        self.assertTrue(err)
        self.assertIn("boom: fix the input", text)

    def test_output_truncated(self):
        (self.skill / "tools" / "echo.py").write_text("print('x' * 20000)", encoding="utf-8")
        text, err = self.call({"course_id": 1})
        self.assertFalse(err)
        self.assertLess(len(text), tt.MAX_OUTPUT_CHARS + 200)
        self.assertIn("[truncated", text)


class TestServer(ToolsCase):
    def test_initialize_list_call(self):
        self.add(manifest())
        reqs = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "demo_echo", "arguments": {"course_id": 3}}},
            {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nope", "arguments": {}}},
        ]
        p = subprocess.run([sys.executable, str(SCRIPTS / "tanka_mcp.py"), str(self.ws)],
                           input="\n".join(json.dumps(r) for r in reqs) + "\n", capture_output=True, text=True, timeout=30)
        out = {m["id"]: m for m in map(json.loads, p.stdout.splitlines())}
        self.assertEqual(sorted(out), [1, 2, 3, 4])
        self.assertEqual(out[1]["result"]["serverInfo"]["name"], "tanka")
        tool = out[2]["result"]["tools"][0]
        self.assertEqual(tool["name"], "demo_echo")
        self.assertIn('Example: {"course_id": 1}', tool["description"])
        self.assertFalse(tool["inputSchema"]["additionalProperties"])
        self.assertTrue(tool["annotations"]["readOnlyHint"])
        self.assertFalse(out[3]["result"]["isError"])
        self.assertIn("'--id=3'", out[3]["result"]["content"][0]["text"])
        self.assertTrue(out[4]["result"]["isError"])

    def test_mcp_config_respects_external_policy(self):
        def lines():
            p = subprocess.run([sys.executable, str(SCRIPTS / "tanka_tools.py"), "mcp-config", str(self.ws), str(REPO / "plugin")],
                               capture_output=True, text=True, check=True)
            return p.stdout.splitlines()
        self.assertEqual(len(lines()), 1)
        pol = self.ws / ".tanka" / "policy.json"
        pol.write_text(json.dumps(dict(json.loads(pol.read_text()), external_mcp="policy")))
        self.assertEqual(lines()[1], str(self.ws / ".tanka" / "mcp.json"))


class TestHookPolicy(ToolsCase):
    def pre(self, tool, tool_input, tid="t1"):
        return run_hook("hook_pre_tool.py", {
            "session_id": "s1", "prompt_id": "p1", "cwd": str(self.ws), "hook_event_name": "PreToolUse",
            "tool_name": tool, "tool_input": tool_input, "tool_use_id": tid}, self.ws)[1]

    def test_tanka_tool_preapproved_with_manifest_class(self):
        self.add(manifest(name="demo_publish", effect="send"))
        self.write_skill("demo_publish")
        out = self.pre("mcp__tanka__demo_publish", {"course_id": 1})
        self.assertEqual(decision(out), "allow")
        self.assertIn("(send)", reason(out))

    def test_unknown_tanka_tool_denied(self):
        self.assertEqual(decision(self.pre("mcp__tanka__demo_ghost", {})), "deny")

    def test_foreign_mcp_denied(self):
        out = self.pre("mcp__claude-in-chrome__navigate", {"url": "https://example.com"})
        self.assertEqual(decision(out), "deny")
        self.assertIn("outside Tanka", reason(out))

    def test_only_tanka_agents(self):
        self.assertEqual(decision(self.pre("Agent", {"subagent_type": "other:browser", "prompt": "x"})), "deny")
        self.assertNotEqual(decision(self.pre("Agent", {"subagent_type": "tanka:tanka-verifier", "prompt": "x"}, "t2")), "deny")

    def test_assistant_cannot_write_tools(self):
        # Pre-approved tools run commands; if the assistant could write a
        # manifest it would have Bash back under another name.
        for i, fp in enumerate([".claude/skills/demo/tools/demo_x.json", ".claude/skills/demo/SKILL.md", ".tanka/policy.json"]):
            out = self.pre("Write", {"file_path": str(self.ws / fp), "content": "{}"}, f"w{i}")
            self.assertEqual(decision(out), "deny", fp)


if __name__ == "__main__":
    unittest.main()
