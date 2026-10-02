"""Routines and triggers: configuration, routing, scheduling and the unattended run, with a fake assistant."""
from __future__ import annotations

import contextlib
import io
import json
import plistlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import tanka_automation as ta  # noqa: E402


class Done:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.stdout, self.returncode, self.stderr = stdout, returncode, stderr


class AutomationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.wsdir = self.tmp / "workspaces"
        self.ws = (self.wsdir / "shop")
        (self.ws / ".tanka").mkdir(parents=True)
        (self.ws / ".tanka" / "policy.json").write_text("{}")
        self.wa_home = self.tmp / "wa"
        self.wa_home.mkdir()
        (self.wa_home / "contacts.json").write_text(json.dumps({"roles": {
            "client": {"workspace": "shop", "read": True, "reply": True},
            "friend": {"workspace": "shop", "read": False, "reply": False}}}))
        patches = {"WORKSPACES": self.wsdir, "STATE_HOME": self.tmp / "state"}
        for k, v in patches.items():
            old = getattr(ta, k)
            setattr(ta, k, v)
            self.addCleanup(setattr, ta, k, old)
        for k, v in {"TANKA_WHATSAPP_HOME": str(self.wa_home), "TANKA_GMAIL_HOME": str(self.tmp / "gm")}.items():
            os.environ[k] = v
            self.addCleanup(os.environ.pop, k, None)
        os.environ.pop("TANKA_HOME", None)
        self.calls = []
        self.now = [1000.0]
        self.alerts = []
        old_alert = ta.alert
        ta.alert = lambda ws, cfg, text: self.alerts.append(text)
        self.addCleanup(setattr, ta, "alert", old_alert)

    def runner(self, report="REPORT: -"):
        def run(cmd):
            self.calls.append(cmd)
            return Done(stdout=f"did things\n{report}\nStatus: done\n")
        return run

    def daemon(self, events=None, report="REPORT: -"):
        feed = events if events is not None else {}
        return ta.Daemon(runner=self.runner(report), events=lambda m: feed.pop(m, []), clock=lambda: self.now[0])


class TestConfig(AutomationCase):
    def test_intervals(self):
        self.assertEqual(ta.seconds("5m"), 300)
        self.assertEqual(ta.seconds("2h"), 7200)
        for bad in ("5", "0m", "30s", "soon"):
            with self.assertRaises(ValueError):
                ta.seconds(bad)

    def test_events_come_from_modules(self):
        self.assertEqual(ta.check_on("whatsapp:client"), ("whatsapp", "client"))
        with self.assertRaisesRegex(ValueError, "not an event"):
            ta.check_on("telegram:x")

    def test_cli_add_list_remove(self):
        cli = lambda *a: ta.main(list(a))
        self.assertEqual(cli("trigger", "add", str(self.ws), "clients", "--on", "whatsapp:client", "Answer them"), 0)
        self.assertEqual(cli("routine", "add", str(self.ws), "morning", "--every", "1d", "Sum up the day"), 0)
        cfg = ta.load(self.ws)
        self.assertEqual(cfg["triggers"]["clients"], {"on": "whatsapp:client", "task": "Answer them"})
        self.assertEqual(cfg["routines"]["morning"]["every"], "1d")
        self.assertEqual(cli("routine", "add", str(self.ws), "bad", "--every", "10s", "x"), 1)
        self.assertEqual(cli("trigger", "remove", str(self.ws), "clients"), 0)
        self.assertNotIn("clients", ta.load(self.ws)["triggers"])

    def test_config_is_outside_what_the_assistant_may_write(self):
        self.assertEqual(ta.config_file(self.ws).relative_to(self.ws).parts[0], ".claude")


class TestObjective(AutomationCase):
    def classes(self):
        oid = ta.objective(self.ws, "x", "shop")
        return json.loads((self.ws / ".tanka" / "objectives" / f"{oid}.json").read_text())["allowed_tool_classes"]

    def test_no_send_unless_a_role_is_opted_in(self):
        self.assertEqual(self.classes(), ["read", "draft"])
        data = json.loads((self.wa_home / "contacts.json").read_text())
        data["roles"]["client"]["auto_reply"] = True
        (self.wa_home / "contacts.json").write_text(json.dumps(data))
        self.assertEqual(self.classes(), ["read", "draft", "send"])


class TestDaemon(AutomationCase):
    def test_trigger_fires_once_per_burst_for_its_role_only(self):
        ta.save(self.ws, {"triggers": {"clients": {"on": "whatsapp:client", "task": "Answer them"}}, "routines": {}})
        ev = lambda n, role: {"source": "whatsapp", "scope": "shop", "role": role, "contact": n, "name": "", "id": n}
        d = self.daemon(events={"whatsapp": [ev("+1", "client"), ev("+2", "client"), ev("+3", "friend")]})
        d.tick()
        self.assertEqual(self.calls, [])  # still inside the burst
        self.now[0] += ta.DEBOUNCE_SECONDS
        d.tick()
        self.assertEqual(len(self.calls), 1)
        task = self.calls[0][2]
        self.assertIn("+1", task)
        self.assertIn("+2", task)
        self.assertNotIn("+3", task)
        self.assertIn("REPORT:", task)
        self.assertIn("--objective", self.calls[0])

    def test_other_workspace_events_are_ignored(self):
        ta.save(self.ws, {"triggers": {"clients": {"on": "whatsapp:*", "task": "x"}}, "routines": {}})
        d = self.daemon(events={"whatsapp": [{"source": "whatsapp", "scope": "work", "role": "student", "contact": "+9", "id": "1"}]})
        d.tick()
        self.now[0] += 60
        d.tick()
        self.assertEqual(self.calls, [])

    def test_routine_runs_on_its_interval(self):
        ta.save(self.ws, {"routines": {"check": {"every": "5m", "task": "Check"}}, "triggers": {}})
        d = self.daemon()
        d.tick()
        self.assertEqual(len(self.calls), 1)
        self.now[0] += 120
        d.tick()
        self.assertEqual(len(self.calls), 1)
        self.now[0] += 200
        d.tick()
        self.assertEqual(len(self.calls), 2)

    def test_report_reaches_the_user_and_silence_does_not(self):
        spec = {"task": "x"}
        self.assertEqual(ta.run_once(self.ws, "routine", "a", spec, "test", self.runner("REPORT: -")), "-")
        self.assertEqual(self.alerts, [])
        self.assertEqual(ta.run_once(self.ws, "routine", "b", spec, "test", self.runner("REPORT: Clara wants a quote")),
                         "Clara wants a quote")
        self.assertEqual(self.alerts, ["b: Clara wants a quote"])
        self.assertIn("Clara wants a quote", (self.ws / ".tanka" / "automation.log").read_text())
        kept = [json.loads(x) for x in (self.ws / ".tanka" / "reports.jsonl").read_text().splitlines()]
        self.assertEqual([(r["kind"], r["name"], r["report"]) for r in kept], [("routine", "b", "Clara wants a quote")])

    def test_the_users_objective_is_put_back(self):
        state = self.ws / ".tanka" / "state"
        state.mkdir(parents=True)
        (state / "objective.json").write_text('{"title": "mine"}')
        def run(cmd):
            (state / "objective.json").write_text('{"title": "automation"}')
            return Done(stdout="REPORT: -")
        ta.run_once(self.ws, "routine", "a", {"task": "x"}, "test", run)
        self.assertEqual((state / "objective.json").read_text(), '{"title": "mine"}')
        (state / "objective.json").unlink()
        ta.run_once(self.ws, "routine", "a", {"task": "x"}, "test", run)
        self.assertFalse((state / "objective.json").exists())

    def test_a_launch_starts_the_daemon_only_when_there_is_work_and_none_runs(self):
        started = []
        for k, v in {"daemon_pid": lambda: None, "start_detached": lambda: started.append(1) or 4242,
                     "service_installed": lambda: False}.items():
            old = getattr(ta, k)
            setattr(ta, k, v)
            self.addCleanup(setattr, ta, k, old)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertIsNone(ta.ensure())  # nothing to run
            (self.ws / ".claude").mkdir(parents=True, exist_ok=True)
            (self.ws / ".claude" / "companion.json").write_text("{}")
            self.assertEqual(ta.ensure(), 4242)  # a companion: its reminders need the poll
            ta.daemon_pid = lambda: 4242
            self.assertIsNone(ta.ensure())  # one already runs
        self.assertEqual(started, [1])

    def service_on(self, platform):
        cmds = []
        patches = {"PLIST_PATH": self.tmp / "agents" / "com.tanka.automation.plist",
                   "UNIT_PATH": self.tmp / "systemd" / "tanka-automation.service", "daemon_pid": lambda: None}
        for k, v in patches.items():
            old = getattr(ta, k)
            setattr(ta, k, v)
            self.addCleanup(setattr, ta, k, old)
        old_platform, old_which = sys.platform, ta.shutil.which
        sys.platform, ta.shutil.which = platform, lambda name: "/usr/bin/" + name
        self.addCleanup(setattr, sys, "platform", old_platform)
        self.addCleanup(setattr, ta.shutil, "which", old_which)
        run = lambda cmd: cmds.append(cmd) or subprocess.CompletedProcess(cmd, 0)
        return cmds, run

    def test_the_service_is_a_launchd_agent_on_macos(self):
        cmds, run = self.service_on("darwin")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ta.service("install", run), 0)
        plist = plistlib.loads(ta.PLIST_PATH.read_bytes())
        self.assertEqual(plist["ProgramArguments"], [str(ta.TANKA), "automation", "daemon"])
        self.assertEqual((plist["RunAtLoad"], plist["KeepAlive"]), (True, {"SuccessfulExit": False}))
        self.assertEqual([c[:2] for c in cmds], [["launchctl", "bootout"], ["launchctl", "bootstrap"]])
        if shutil.which("plutil"):
            self.assertEqual(subprocess.run(["plutil", "-lint", str(ta.PLIST_PATH)], capture_output=True).returncode, 0)
        with contextlib.redirect_stdout(io.StringIO()):
            ta.service("remove", run)
        self.assertFalse(ta.PLIST_PATH.exists())

    def test_the_service_is_a_systemd_unit_on_linux(self):
        cmds, run = self.service_on("linux")
        self.assertEqual(ta.service("install", run), 0)
        unit = ta.UNIT_PATH.read_text()
        self.assertIn(f"ExecStart={ta.TANKA} automation daemon", unit)
        self.assertIn(["systemctl", "--user", "enable", "--now", "tanka-automation.service"], cmds)
        with contextlib.redirect_stdout(io.StringIO()):
            ta.service("remove", run)
        self.assertFalse(ta.UNIT_PATH.exists())

    def test_with_the_service_installed_stop_and_launch_go_through_it(self):
        calls, pid = [], [4242]
        for k, v in {"daemon_pid": lambda: pid[0], "service_installed": lambda: True,
                     "service_stop": lambda: calls.append("stop") or pid.__setitem__(0, None),
                     "service_start": lambda: calls.append("start") or pid.__setitem__(0, 5151) or True,
                     "start_detached": lambda: calls.append("detached")}.items():
            old = getattr(ta, k)
            setattr(ta, k, v)
            self.addCleanup(setattr, ta, k, old)
        (self.ws / ".claude").mkdir(parents=True, exist_ok=True)
        (self.ws / ".claude" / "companion.json").write_text("{}")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(ta.main(["stop"]), 0)  # a SIGTERM would only make launchd restart it
            self.assertEqual(ta.ensure(), 5151)
        self.assertEqual(calls, ["stop", "start"])

    def test_hourly_cap(self):
        ta.save(self.ws, {"routines": {"r": {"every": "1m", "task": "x"}}, "triggers": {}})
        d = self.daemon()
        for _ in range(ta.MAX_RUNS_PER_HOUR + 3):
            d.tick()
            self.now[0] += 61
        self.assertEqual(len(self.calls), ta.MAX_RUNS_PER_HOUR)


class TestUnattendedSends(unittest.TestCase):
    def test_whatsapp_refuses_roles_without_auto_reply(self):
        sys.path.insert(0, str(REPO / "modules" / "whatsapp"))
        import wa
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        (tmp / "contacts.json").write_text(json.dumps({
            "roles": {"client": {"workspace": "shop", "read": True, "reply": True}},
            "contacts": {"+15550001111": {"name": "C", "role": "client"}}}))
        old = wa.REGISTRY, wa.session
        wa.REGISTRY = tmp / "contacts.json"
        wa.session = lambda: (_ for _ in ()).throw(AssertionError("the browser must not be used"))
        self.addCleanup(lambda: (setattr(wa, "REGISTRY", old[0]), setattr(wa, "session", old[1])))
        os.environ["TANKA_UNATTENDED"] = "1"
        self.addCleanup(os.environ.pop, "TANKA_UNATTENDED", None)
        with self.assertRaisesRegex(wa.ToolError, "not set to auto_reply"):
            wa.reply("shop", "+15550001111", "hi")


class TestLauncher(unittest.TestCase):
    def test_trigger_names_resolve_and_validate(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        env = dict(os.environ, TANKA_WORKSPACES=str(tmp / "ws"))
        env.pop("TANKA_HOME", None)
        tanka = lambda *a: subprocess.run([str(REPO / "bin" / "tanka"), *a], capture_output=True, text=True, env=env)
        tanka("init", "shop")
        ok = tanka("trigger", "add", "shop", "clients", "--on", "whatsapp:client", "Answer them")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue((tmp / "ws" / "shop" / ".claude" / "automations.json").is_file())
        bad = tanka("trigger", "add", "shop", "x", "--on", "fax:y", "z")
        self.assertEqual(bad.returncode, 1)
        self.assertIn("not an event", bad.stderr)


if __name__ == "__main__":
    unittest.main()
