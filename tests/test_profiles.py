"""Workspace profiles: haiku keeps the strict harness byte for byte, sonnet relaxes only what a stronger
model does not need, and the safety invariants are the same in both."""
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
sys.path.insert(0, str(Path(__file__).resolve().parent))
import hook_user_prompt as hup  # noqa: E402
import tanka_common as tc  # noqa: E402
import tanka_tools as tt  # noqa: E402
from test_hooks import TEMPLATE  # noqa: E402
from test_tools import manifest  # noqa: E402

LAUNCHER = REPO / "bin" / "tanka"
SAFETY_KEYS = ("tool_classes", "decisions", "send_validation", "external_mcp", "objective")


class ProfileCase(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp(prefix="tanka-profile-")).resolve()
        self.addCleanup(shutil.rmtree, self.ws, True)
        shutil.copytree(TEMPLATE, self.ws, dirs_exist_ok=True)
        (self.ws / ".tanka" / "policy.json").write_text("{}", encoding="utf-8")  # no customisation: defaults only

    def set_profile(self, name):
        (self.ws / ".tanka" / "workspace.json").write_text(json.dumps({"profile": name}), encoding="utf-8")

    def add_skill(self, skill: str, tools: int, params: int = 1):
        d = self.ws / ".claude" / "skills" / skill / "tools"
        d.mkdir(parents=True, exist_ok=True)
        names = []
        for i in range(tools):
            name = f"{skill}_tool{i}"
            spec = {f"p{j}": {"type": "string", "description": "a parameter here"} for j in range(params)}
            (d / f"{name}.json").write_text(json.dumps(manifest(name=name, params=spec, examples=[{}],
                                                                run=["python3", "x.py"])), encoding="utf-8")
            names.append(name)
        (d / "x.py").write_text("print('ok')", encoding="utf-8")
        (d.parent / "SKILL.md").write_text(f"---\nname: {skill}\ndescription: demo skill for tests\n---\n" + " ".join(names),
                                           encoding="utf-8")


class TestProfileName(ProfileCase):
    def test_no_file_is_the_strict_profile(self):
        self.assertEqual(tc.profile_name(self.ws), "haiku")
        self.assertEqual(tc.profile_name(None), "haiku")

    def test_unreadable_or_unknown_is_the_strict_profile(self):
        (self.ws / ".tanka" / "workspace.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(tc.profile_name(self.ws), "haiku")
        self.set_profile("opus")
        self.assertEqual(tc.profile_name(self.ws), "haiku")
        (self.ws / ".tanka" / "workspace.json").write_text('["sonnet"]', encoding="utf-8")
        self.assertEqual(tc.profile_name(self.ws), "haiku")

    def test_sonnet(self):
        self.set_profile("sonnet")
        self.assertEqual(tc.profile_name(self.ws), "sonnet")


class TestPolicy(ProfileCase):
    def test_haiku_is_exactly_the_default_policy(self):
        self.assertEqual(tc.load_policy(self.ws), tc.DEFAULT_POLICY)

    def test_sonnet_relaxes_the_loop_guard_and_the_reminder(self):
        self.set_profile("sonnet")
        policy = tc.load_policy(self.ws)
        self.assertGreater(policy["loop_guard"]["max_calls_per_turn"], tc.DEFAULT_POLICY["loop_guard"]["max_calls_per_turn"])
        self.assertLess(len(policy["hard_rules"]), len(tc.DEFAULT_POLICY["hard_rules"]))

    def test_safety_invariants_do_not_depend_on_the_model(self):
        self.set_profile("sonnet")
        policy = tc.load_policy(self.ws)
        for key in SAFETY_KEYS:
            self.assertEqual(policy[key], tc.DEFAULT_POLICY[key], key)
        self.assertEqual(policy["decisions"]["destructive"], "deny")

    def test_the_reminder_keeps_honesty_and_confirmation_rules(self):
        self.set_profile("sonnet")
        rules = " ".join(tc.load_policy(self.ws)["hard_rules"]).lower()
        for needle in ("tool result", "explicit yes", "status:"):
            self.assertIn(needle, rules)

    def test_the_users_own_policy_always_wins(self):
        self.set_profile("sonnet")
        (self.ws / ".tanka" / "policy.json").write_text(json.dumps({"loop_guard": {"max_calls_per_turn": 7}}), encoding="utf-8")
        self.assertEqual(tc.load_policy(self.ws)["loop_guard"]["max_calls_per_turn"], 7)
        self.assertEqual(tc.load_policy(self.ws)["loop_guard"]["max_consecutive_failures"], 4)

    def test_a_policy_that_only_spells_out_the_defaults_does_not_hide_the_profile(self):
        self.set_profile("sonnet")
        (self.ws / ".tanka" / "policy.json").write_text(json.dumps({"loop_guard": tc.DEFAULT_POLICY["loop_guard"]}), encoding="utf-8")
        self.assertEqual(tc.load_policy(self.ws)["loop_guard"]["max_calls_per_turn"], 60)

    def test_the_shipped_template_leaves_the_loop_guard_to_the_profile(self):
        shipped = json.loads((TEMPLATE / ".tanka" / "policy.json").read_text())
        self.assertNotIn("loop_guard", shipped)

    def test_the_turn_reminder_is_shorter_for_sonnet(self):
        strict = hup.build_context(tc.load_policy(self.ws), tc.load_persona(self.ws), None)
        self.set_profile("sonnet")
        light = hup.build_context(tc.load_policy(self.ws), tc.load_persona(self.ws), None)
        self.assertLess(len(light), len(strict))
        self.assertNotIn("not a programmer", light)


class TestLimits(ProfileCase):
    def test_haiku_uses_the_module_constants(self):
        self.assertEqual(tt.limits(self.ws), {"total": 15, "per_skill": 6, "params": 6, "required": 4})
        self.assertEqual(tt.limits(None), tt.limits(self.ws))

    def test_haiku_still_honours_a_patched_constant(self):
        old = tt.MAX_TOOLS_TOTAL
        tt.MAX_TOOLS_TOTAL = 2
        self.addCleanup(setattr, tt, "MAX_TOOLS_TOTAL", old)
        self.assertEqual(tt.tools_max(self.ws), 2)

    def test_sonnet_gets_the_wide_budget(self):
        self.set_profile("sonnet")
        self.assertEqual(tt.limits(self.ws), {"total": 30, "per_skill": 10, "params": 8, "required": 6})

    def test_a_skill_of_eight_tools_loads_for_sonnet_only(self):
        self.add_skill("big", 8)
        tools, problems = tt.scan(self.ws)
        self.assertEqual(len(tools), 0)
        self.assertTrue(any("limit is 6" in p for p in problems), problems)
        self.set_profile("sonnet")
        tools, problems = tt.scan(self.ws)
        self.assertEqual((len(tools), problems), (8, []))

    def test_twenty_tools_in_total(self):
        for i in range(4):
            self.add_skill(f"s{i}", 5)
        self.assertEqual(len(tt.scan(self.ws)[0]), 15)
        self.set_profile("sonnet")
        self.assertEqual(len(tt.scan(self.ws)[0]), 20)

    def test_seven_params_are_fine_for_sonnet_and_not_for_haiku(self):
        m = manifest(params={f"p{i}": {"type": "string", "description": "a parameter here"} for i in range(7)},
                     examples=[{}], run=["python3", "x.py"])
        self.assertTrue(tt.validate_manifest(m, "demo", "demo_echo"))
        self.assertEqual(tt.validate_manifest(m, "demo", "demo_echo", tt.limits_for_profile("sonnet")), [])

    def test_description_rules_are_the_same_in_every_profile(self):
        self.set_profile("sonnet")
        short = manifest(description="Too short.")
        self.assertTrue(tt.validate_manifest(short, "demo", "demo_echo", tt.limits(self.ws)))

    def test_profile_subcommand_prints_what_the_launcher_needs(self):
        def line():
            return subprocess.run([sys.executable, str(REPO / "plugin" / "scripts" / "tanka_tools.py"), "profile", str(self.ws)],
                                  capture_output=True, text=True).stdout.strip()
        self.assertEqual(line(), "haiku haiku 60 false")
        self.set_profile("sonnet")
        self.assertEqual(line(), "sonnet sonnet 75 auto")


class TestLauncher(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="tanka-launch-")).resolve()
        self.addCleanup(shutil.rmtree, self.home, True)
        self.env = {**os.environ, "TANKA_WORKSPACES": str(self.home), "TANKA_NO_DAEMON": "1"}
        self.env.pop("TANKA_INIT_MODEL", None)
        self.env.pop("TANKA_MODEL", None)

    def tanka(self, *args, env=None):
        return subprocess.run(["bash", str(LAUNCHER), *args], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              env={**self.env, **(env or {})})

    def profile_file(self, name):
        return json.loads((self.home / name / ".tanka" / "workspace.json").read_text())["profile"]

    def test_init_with_the_flag(self):
        self.assertEqual(self.tanka("init", "mei", "--model", "sonnet").returncode, 0)
        self.assertEqual(self.profile_file("mei"), "sonnet")
        self.assertIn("sonnet", self.tanka("profile", "mei").stdout)

    def test_init_without_a_terminal_or_a_choice_is_strict(self):
        self.assertEqual(self.tanka("init", "plain").returncode, 0)
        self.assertEqual(self.profile_file("plain"), "haiku")

    def test_init_reads_the_environment_variable(self):
        self.assertEqual(self.tanka("init", "viaenv", env={"TANKA_INIT_MODEL": "Sonnet"}).returncode, 0)
        self.assertEqual(self.profile_file("viaenv"), "sonnet")

    def test_an_unknown_model_creates_nothing(self):
        r = self.tanka("init", "bad", "--model", "gpt")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("haiku or sonnet", r.stderr)
        self.assertFalse((self.home / "bad").exists())

    def test_profile_changes_a_workspace_and_rejects_nonsense(self):
        self.tanka("init", "w")
        self.assertEqual(self.tanka("profile", "w", "sonnet").returncode, 0)
        self.assertEqual(self.profile_file("w"), "sonnet")
        self.assertNotEqual(self.tanka("profile", "w", "banana").returncode, 0)
        self.assertEqual(self.profile_file("w"), "sonnet")

    def test_a_workspace_from_before_profiles_is_strict(self):
        self.tanka("init", "old")
        (self.home / "old" / ".tanka" / "workspace.json").unlink()
        self.assertTrue(self.tanka("profile", "old").stdout.startswith("haiku"))

    def test_profile_is_not_a_workspace_name(self):
        self.assertNotEqual(self.tanka("init", "profile").returncode, 0)


if __name__ == "__main__":
    unittest.main()
