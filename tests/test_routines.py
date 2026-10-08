"""Routines module: proposals the assistant drafts, the user's approval, limits, scopes, the tools, install."""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "modules" / "routines"))
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import routines as r  # noqa: E402
import tanka_automation as ta  # noqa: E402
import tanka_tools as tt  # noqa: E402
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location("routines_cli", REPO / "modules" / "routines" / "cli.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)
TASK = "Summarise the open issues of PROJ every morning"


def out_of(fn, *args, **kw) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args, **kw)
    return buf.getvalue()


class RoutinesCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home = self.tmp / "routines"
        self.wsdir = self.tmp / "workspaces"
        self.ws = self.make_ws("duck")
        self.other = self.make_ws("goose")
        for obj, name, value in ((r, "HOME", self.home), (ta, "WORKSPACES", self.wsdir),
                                 (ta, "daemon_pid", lambda: None)):
            old = getattr(obj, name)
            setattr(obj, name, value)
            self.addCleanup(setattr, obj, name, old)
        for var in list(r.LIMIT_ENV.values()) + ["TANKA_WORKSPACE"]:
            old = os.environ.pop(var, None)
            if old is not None:
                self.addCleanup(os.environ.__setitem__, var, old)

    def make_ws(self, name: str) -> Path:
        ws = self.wsdir / name
        (ws / ".tanka").mkdir(parents=True)
        (ws / ".tanka" / "policy.json").write_text("{}")
        (ws / ".claude").mkdir()
        return ws

    def propose(self, name="morning-issues", every="1d", task=TASK, budget=0.25, why="You ask daily", scope="duck", ws=None):
        return r.propose(scope, ws or self.ws, name, every, task, budget, why)

    def set_limits(self, **kw):
        self.home.mkdir(parents=True, exist_ok=True)
        (self.home / "limits.json").write_text(json.dumps(kw))


class TestValidation(RoutinesCase):
    def test_names(self):
        for bad in ("A", "x", "1abc", "has space", "a" * 33, "under_score", "../x", None):
            with self.assertRaisesRegex(r.ToolError, "not a routine name"):
                self.propose(name=bad)
        self.assertEqual(self.propose(name="ab")["status"], "pending")

    def test_name_taken_by_routine_trigger_or_pending(self):
        ta.save(self.ws, {"routines": {"daily": {"every": "1d", "task": "x"}},
                          "triggers": {"mail": {"on": "gmail:*", "task": "y"}}})
        with self.assertRaisesRegex(r.ToolError, "already a routine"):
            self.propose(name="daily")
        with self.assertRaisesRegex(r.ToolError, "already a trigger"):
            self.propose(name="mail")
        self.propose(name="digest")
        with self.assertRaisesRegex(r.ToolError, "already a pending proposal"):
            self.propose(name="digest")

    def test_interval(self):
        for bad in ("soon", "", "0h", "1x", None):
            with self.assertRaisesRegex(r.ToolError, "not an interval"):
                self.propose(every=bad)
        with self.assertRaisesRegex(r.ToolError, "too often"):
            self.propose(every="30m")
        self.assertEqual(self.propose(every="60m")["every"], "60m")

    def test_budget(self):
        for bad in (0, -1, 0.51, "0.2", True, float("nan")):
            with self.assertRaises(r.ToolError):
                self.propose(budget=bad, name="b-" + str(abs(hash(str(bad))) % 999))
        self.assertEqual(self.propose(budget=0.5)["budget"], 0.5)

    def test_task(self):
        with self.assertRaisesRegex(r.ToolError, "Say what"):
            self.propose(task="   ")
        with self.assertRaisesRegex(r.ToolError, "limit is 800"):
            self.propose(task="x" * 801)
        with self.assertRaisesRegex(r.ToolError, "control characters"):
            self.propose(task="read\x1b[2Jthis")
        self.assertIn("\n", self.propose(task="line one\nline two")["task"])

    def test_why(self):
        with self.assertRaisesRegex(r.ToolError, "one line"):
            self.propose(why="a\nb")
        with self.assertRaisesRegex(r.ToolError, "one line"):
            self.propose(why="x" * 201)

    def test_max_pending(self):
        for i in range(5):
            self.propose(name=f"job-{i}")
        with self.assertRaisesRegex(r.ToolError, "already 5 proposals"):
            self.propose(name="job-5")

    def test_scope_names_refused(self):
        for bad in ("../duck", "Duck", "a/b", "", ".hidden"):
            with self.assertRaisesRegex(r.ToolError, "not a workspace scope"):
                self.propose(scope=bad)
            with self.assertRaises(r.ToolError):
                r.proposals_file(bad)


class TestApproval(RoutinesCase):
    def test_approve_writes_a_routine_the_daemon_accepts(self):
        ta.save(self.ws, {"routines": {"mine": {"every": "2h", "task": "x"}}, "triggers": {},
                          "alert": {"notify": False}})
        p = self.propose()
        msg = r.approve("duck", self.ws, p["id"])
        self.assertIn("morning-issues is active", msg)
        raw = json.loads(ta.config_file(self.ws).read_text())
        self.assertEqual(raw["alert"], {"notify": False})  # other keys kept
        spec = raw["routines"]["morning-issues"]
        self.assertEqual(spec, {"every": "1d", "task": TASK, "budget": "0.25", "proposed_by": "assistant"})
        cfg = ta.load(self.ws)
        self.assertEqual(ta.seconds(cfg["routines"]["morning-issues"]["every"]), 86400)
        # The daemon's own run path builds its command from this spec without complaint.
        seen = []
        runner = lambda cmd: seen.append(cmd) or subprocess.CompletedProcess(cmd, 0, "REPORT: -", "")
        self.assertEqual(ta.run_once(self.ws, "routine", "morning-issues", spec, "test", runner), "-")
        self.assertIn("0.25", seen[0])
        s = r.state("duck", self.ws)
        self.assertEqual({x["name"]: x["source"] for x in s["routines"]}, {"mine": "you", "morning-issues": "assistant"})
        self.assertEqual(s["routines"][1]["last"]["ok"], True)
        self.assertEqual(s["proposals"][0]["status"], "approved")
        with self.assertRaisesRegex(r.ToolError, "approved; only a pending"):
            r.approve("duck", self.ws, p["id"])
        self.assertFalse(list(self.ws.glob(".claude/.*.tmp")))

    def test_approve_rechecks_current_limits(self):
        p = self.propose(budget=0.5, every="1h")
        self.set_limits(max_budget_usd=0.3)
        with self.assertRaisesRegex(r.ToolError, "at most 0.3"):
            r.approve("duck", self.ws, p["id"])
        self.set_limits(min_interval_seconds=7200)
        with self.assertRaisesRegex(r.ToolError, "too often"):
            r.approve("duck", self.ws, p["id"])
        self.assertFalse(ta.config_file(self.ws).exists())

    def test_max_automations_and_name_taken_at_approval(self):
        q = self.propose(name="late")
        ta.save(self.ws, {"routines": {"late": {"every": "1d", "task": "user made it first"}}, "triggers": {}})
        with self.assertRaisesRegex(r.ToolError, "already a routine"):
            r.approve("duck", self.ws, q["id"])
        self.set_limits(max_automations=1)
        p = self.propose(name="another")
        with self.assertRaisesRegex(r.ToolError, "limit 1"):
            r.approve("duck", self.ws, p["id"])
        self.assertEqual(ta.load(self.ws)["routines"]["late"]["task"], "user made it first")  # never changed

    def test_reject_withdraw_remove(self):
        a, b = self.propose(name="aaa"), self.propose(name="bbb")
        self.assertIn("rejected", r.reject("duck", self.ws, a["id"]))
        with self.assertRaisesRegex(r.ToolError, "rejected, not pending"):
            r.withdraw("duck", a["id"])
        self.assertEqual(r.withdraw("duck", b["id"])["status"], "withdrawn")
        with self.assertRaisesRegex(r.ToolError, "withdrawn; only a pending"):
            r.approve("duck", self.ws, b["id"])
        with self.assertRaisesRegex(r.ToolError, "No proposal"):
            r.withdraw("duck", "p-000000")
        c = self.propose(name="ccc")
        r.approve("duck", self.ws, c["id"])
        r.withdraw("duck", self.propose(name="ddd")["id"])
        with self.assertRaisesRegex(r.ToolError, "approved, not pending"):
            r.withdraw("duck", c["id"])
        ta.save(self.ws, {**ta.load(self.ws), "triggers": {"mail": {"on": "gmail:*", "task": "y"}}})
        with self.assertRaisesRegex(r.ToolError, "no routine named mail"):
            r.remove("duck", self.ws, "mail")  # triggers are not routines
        self.assertIn("removed", r.remove("duck", self.ws, "ccc"))
        cfg = ta.load(self.ws)
        self.assertEqual((cfg["routines"], list(cfg["triggers"])), ({}, ["mail"]))
        with self.assertRaisesRegex(r.ToolError, "no routine named ccc"):
            r.remove("duck", self.ws, "ccc")

    def test_scopes_are_isolated(self):
        p = self.propose()
        self.assertEqual(r.state("goose", self.other)["proposals"], [])
        with self.assertRaisesRegex(r.ToolError, "No proposal"):
            r.approve("goose", self.other, p["id"])
        with self.assertRaisesRegex(r.ToolError, "No proposal"):
            r.withdraw("goose", p["id"])
        with self.assertRaisesRegex(r.ToolError, "No proposal"):
            r.reject("goose", self.other, p["id"])
        self.propose(scope="goose", ws=self.other)  # same name, other scope: allowed
        self.assertFalse(ta.config_file(self.other).exists())

    def test_only_twenty_kept_pending_never_dropped(self):
        for i in range(25):
            r.withdraw("duck", self.propose(name=f"old-{i}")["id"])
        keep = self.propose(name="keep")
        s = r.state("duck", self.ws)["proposals"]
        self.assertEqual(len(s), 20)
        self.assertEqual(s[0]["id"], keep["id"])


class TestFilesAndLimits(RoutinesCase):
    def test_limits_overrides(self):
        self.assertEqual(r.limits(), r.DEFAULT_LIMITS)
        self.set_limits(max_budget_usd=1.5, max_pending="7", min_interval_seconds=-5, max_automations=True,
                        max_task_chars=100, unknown=3)
        lim = r.limits()
        self.assertEqual(lim["max_budget_usd"], 1.5)
        self.assertEqual((lim["max_pending"], lim["min_interval_seconds"], lim["max_automations"]), (5, 3600, 8))
        self.assertEqual(lim["max_task_chars"], 100)
        self.assertNotIn("unknown", lim)
        os.environ["TANKA_ROUTINES_MAX_PENDING"] = "2"
        self.addCleanup(os.environ.pop, "TANKA_ROUTINES_MAX_PENDING", None)
        self.assertEqual(r.limits()["max_pending"], 2)
        (self.home / "limits.json").write_text("{not json")
        self.assertEqual(r.limits()["max_pending"], 2)

    def test_corrupt_proposals_file(self):
        f = r.proposals_file("duck")
        f.parent.mkdir(parents=True)
        f.write_text("{broken")
        s = r.state("duck", self.ws)
        self.assertEqual(s["proposals"], [])
        self.assertIn("not valid JSON", s["notice"])
        self.assertIn("not valid JSON", out_of(r.list_tool, "duck", self.ws))
        p = self.propose()
        self.assertTrue(f.with_name(f.name + ".corrupt").is_file())
        self.assertEqual(r.state("duck", self.ws)["proposals"][0]["id"], p["id"])
        f.write_text('{"proposals": "nope"}')
        self.assertEqual(r.read_proposals("duck")[0], [])

    def test_corrupt_automations_is_a_clear_error(self):
        ta.config_file(self.ws).write_text("{")
        with self.assertRaisesRegex(r.ToolError, "cannot be read"):
            r.state("duck", self.ws)

    def test_history_and_output_cap(self):
        log = self.ws / ".tanka" / "automation.log"
        lines = [f"2026-10-0{1 + i % 9} 08:00:00 routine daily | the routine \"daily\" (every 1d) | exit {i % 2} | "
                 + ("r" * 400) for i in range(40)]
        lines.append("2026-10-09 09:00:00 trigger mail | skipped: over 20 runs this hour (X)")
        log.write_text("garbage line\n" + "\n".join(lines) + "\n")
        out = out_of(r.history_tool, "duck", self.ws, 99)
        self.assertTrue(out.startswith("30 run(s), newest first:"))
        self.assertIn("trigger mail | failed | skipped", out.splitlines()[1])
        self.assertLessEqual(len(out), r.MAX_OUTPUT + 1)
        self.assertIn("…", out)
        self.assertEqual(r.cap("x" * 9000)[-40:].count("truncated"), 1)
        self.assertLessEqual(len(r.cap("x" * 9000)), r.MAX_OUTPUT)
        self.assertIn("No runs", out_of(r.history_tool, "goose", self.other))

    def test_list_and_propose_output(self):
        out = out_of(r.propose_tool, "duck", self.ws, "morning-issues", "1d", TASK)
        self.assertIn("NOT active", out)
        self.assertIn("tanka routines approve duck p-", out)
        listing = out_of(r.list_tool, "duck", self.ws)
        self.assertTrue(listing.startswith("0 routine(s), 0 trigger(s), 1 pending proposal(s)"))
        self.assertIn("NOT active until the user approves", listing)
        for _ in range(30):
            ta.save(self.ws, {**ta.load(self.ws), "triggers": {f"t{i}": {"on": "gmail:*", "task": "z" * 400}
                                                               for i in range(60)}})
        self.assertLessEqual(len(out_of(r.list_tool, "duck", self.ws)), r.MAX_OUTPUT + 1)


class TestCli(RoutinesCase):
    def cli(self, *argv, answer=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err), \
                unittest.mock.patch("builtins.input", lambda _: answer if answer is not None else ""):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_approve_shows_everything_and_asks(self):
        p = self.propose(task="first line\nsecond line")
        code, out, _ = self.cli("approve", str(self.ws), p["id"], answer="n")
        self.assertEqual(code, 1)
        for part in ("morning-issues", "every 1d", "0.25 USD", "first line", "second line", "Not activated"):
            self.assertIn(part, out)
        self.assertFalse(ta.config_file(self.ws).exists())
        code, out, _ = self.cli("approve", str(self.ws), p["id"], "--yes")
        self.assertEqual(code, 0, out)
        self.assertIn("morning-issues", ta.load(self.ws)["routines"])

    def test_commands(self):
        p = self.propose()
        self.assertIn(p["id"], self.cli("proposals", "duck")[1])
        self.assertIn("1 pending", self.cli("list", "duck")[1])
        self.assertEqual(self.cli("reject", "duck", p["id"])[0], 0)
        self.assertEqual(self.cli("remove", "duck", "nothing")[0], 1)
        self.assertIn("No runs", self.cli("history", "duck")[1])
        code, _, err = self.cli("list", "nowhere")
        self.assertEqual(code, 1)
        self.assertIn("not a Tanka workspace", err)
        self.assertEqual(self.cli("post-install", str(self.ws), "duck")[0], 0)
        self.assertTrue((self.home / "proposals").is_dir())


class TestToolsAndInstall(unittest.TestCase):
    def test_manifests_valid(self):
        tools = sorted((REPO / "modules" / "routines" / "skill" / "tools").glob("*.json"))
        self.assertEqual([t.stem for t in tools], ["routines_history", "routines_list", "routines_propose", "routines_withdraw"])
        for f in tools:
            self.assertEqual(tt.validate_manifest(json.loads(f.read_text()), "routines", f.stem), [], f.name)
            self.assertIn('SCOPE = "__SCOPE__"', f.with_suffix(".py").read_text())

    def test_install_check_and_run_tools(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        env = dict(os.environ, TANKA_WORKSPACES=str(tmp / "workspaces"), TANKA_ROUTINES_HOME=str(tmp / "home"),
                   TANKA_AUTOMATION_HOME=str(tmp / "automation"))
        tanka = lambda *a: subprocess.run([str(REPO / "bin" / "tanka"), *a], capture_output=True, text=True, env=env)
        tanka("init", "duck")
        p = tanka("install", "routines", "duck")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("tanka routines approve duck", p.stdout)
        ws = tmp / "workspaces" / "duck"
        self.assertIn('SCOPE = "duck"', (ws / ".claude/skills/routines/tools/routines_propose.py").read_text())
        check = tanka("tools", "check", "duck")
        self.assertIn("4/15 tools loaded, 0 problem(s), 0 warning(s)", check.stdout)
        test = tanka("tools", "test", "routines_propose",
                     json.dumps({"name": "morning-issues", "every": "1d", "task": TASK}), "duck")
        self.assertIn("NOT active", test.stdout + test.stderr)
        self.assertTrue((tmp / "home" / "proposals" / "duck.json").is_file())
        self.assertFalse((ws / ".claude" / "automations.json").exists())
        listing = tanka("tools", "test", "routines_list", "{}", "duck")
        self.assertIn("1 pending proposal(s)", listing.stdout + listing.stderr)


if __name__ == "__main__":
    unittest.main()
