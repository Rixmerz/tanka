"""Codepanion module: the tap's scope and scrubbing, signals, lens rules, notes, the backtest and install."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "modules" / "codepanion"))
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
import codepanion as c  # noqa: E402
import chat  # noqa: E402
import ui  # noqa: E402
import tanka_automation as ta  # noqa: E402
import tanka_modules as tm  # noqa: E402
import importlib.util  # noqa: E402

# Loaded under its own name: other modules have a cli.py too.
_spec = importlib.util.spec_from_file_location("codepanion_cli", REPO / "modules" / "codepanion" / "cli.py")
cli = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cli)

EXAMPLES = REPO / "builder" / "skills" / "new-codepanion" / "examples"
FAKE_KEY = "sk-" + "ant-" + "x" * 24  # built at runtime, so no scanner sees a key in this file


def out_of(fn, *args) -> str:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fn(*args)
    return buf.getvalue()


class CodepanionCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.home, self.wsdir = self.tmp / "home", self.tmp / "workspaces"
        self.project = self.tmp / "code" / "app"
        (self.project / "src").mkdir(parents=True)
        self.other = self.tmp / "code" / "other"
        self.other.mkdir(parents=True)
        self.ws = self.wsdir / "duck"
        (self.ws / ".tanka").mkdir(parents=True)
        (self.ws / ".tanka" / "policy.json").write_text("{}")
        for k, v in {"HOME": self.home, "WORKSPACES": self.wsdir, "CLAUDE_HOME": self.tmp / "claude"}.items():
            old = getattr(c, k)
            setattr(c, k, v)
            self.addCleanup(setattr, c, k, old)
        c.watch_set(str(self.project), "duck")
        self.t = [time.time()]
        old_now = c.now
        c.now = lambda: self.t[0]
        self.addCleanup(setattr, c, "now", old_now)

    def configure(self, lenses=("stuck",), **extra):
        c.lenses_dir(self.ws).mkdir(parents=True, exist_ok=True)
        for name in lenses:
            if (EXAMPLES / f"{name}.md").is_file():
                shutil.copy(EXAMPLES / f"{name}.md", c.lenses_dir(self.ws) / f"{name}.md")
        c.write_json(c.config_file(self.ws), {"lenses": list(lenses), "proactivity": 1, **extra})

    def hook(self, name, cwd=None, session="s1", **fields):
        c.tap({"hook_event_name": name, "cwd": str(cwd or self.project), "session_id": session, **fields})
        self.t[0] += 1

    def feed(self, session="s1"):
        return c.read_feed(c.feed_dir() / f"{session}.jsonl")

    def fail(self, error="Traceback\nKeyError: 'user_id'", session="s1"):
        self.hook("PostToolUseFailure", session=session, tool_name="Bash", tool_input={"command": "pytest -q"}, error=error)


class TestTap(CodepanionCase):
    def test_only_watched_projects_reach_the_feed(self):
        self.hook("UserPromptSubmit", cwd=self.other, session="elsewhere", prompt="hello")
        self.hook("UserPromptSubmit", cwd=self.project / "src", prompt="fix the login")
        self.assertFalse((c.feed_dir() / "elsewhere.jsonl").exists())
        self.assertEqual(self.feed()[0]["text"], "fix the login")
        self.assertEqual(self.feed()[0]["project"], str(self.project))

    def test_a_sibling_with_the_same_prefix_is_not_watched(self):
        sibling = self.tmp / "code" / "app-old"
        sibling.mkdir()
        self.assertIsNone(c.owner(sibling))

    def test_secrets_never_reach_the_feed(self):
        self.hook("UserPromptSubmit", prompt=f"use the key {FAKE_KEY} please")
        login = "bob" + ":" + "changeme-fake"  # built in parts: no credential-looking URL in the repo
        self.hook("PostToolUse", tool_name="Bash", tool_input={"command": f"curl https://{login}@example.com -H 'Authorization: Bearer xxxxxxxxxxxxxxxxxxxxxx'"})
        raw = (c.feed_dir() / "s1.jsonl").read_text()
        for leaked in (FAKE_KEY, "changeme-fake", "xxxxxxxxxxxxxxxxxxxxxx"):
            self.assertNotIn(leaked, raw)
        self.assertIn("[secret]", raw)

    def test_edits_count_lines_and_files(self):
        self.hook("PostToolUse", tool_name="Edit", tool_input={"file_path": "src/a.py", "old_string": "x", "new_string": "a\nb\nc"})
        ev = self.feed()[-1]
        self.assertEqual((ev["lines"], ev["file"], ev["ok"]), (3, "src/a.py", True))

    def test_the_hook_command_is_silent_and_never_fails(self):
        env = dict(os.environ, TANKA_CODEPANION_HOME=str(self.home))
        for payload in ("not json", json.dumps({"hook_event_name": "Stop", "cwd": str(self.project), "session_id": "s9"})):
            p = subprocess.run([sys.executable, str(REPO / "modules" / "codepanion" / "tap.py")], input=payload,
                               capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual((p.returncode, p.stdout, p.stderr), (0, "", ""))
        self.assertEqual(c.read_feed(c.feed_dir() / "s9.jsonl")[0]["e"], "turn_end")


class TestSignals(CodepanionCase):
    def test_stuck_fires_once_on_the_third_identical_failure(self):
        self.hook("UserPromptSubmit", prompt="run the tests")
        for _ in range(5):
            self.fail(error=f"Exit code 1\nFile x.py line {int(self.t[0]) % 97}\nKeyError: 'user_id'")
        fs = [f for f in c.firings(self.feed(), c.DEFAULT_THRESHOLDS) if f["signal"] == "stuck"]
        self.assertEqual(len(fs), 1)
        self.assertIn("KeyError", fs[0]["evidence"])

    def test_the_last_line_of_the_error_decides(self):
        for i in range(3):
            self.fail(error=f"Exit code 1\nrunning test_{i} with seed {i * 7919}\n  File \"x.py\", line {i}\nKeyError: 'user_id'")
        fs = c.firings(self.feed(), c.DEFAULT_THRESHOLDS)
        self.assertEqual([f["signal"] for f in fs], ["stuck"])
        self.assertTrue(self.feed()[-1]["error"].endswith("KeyError: 'user_id'"))

    def test_different_errors_are_not_stuck(self):
        for err in ("KeyError: 'a'", "TypeError: x", "ValueError: y"):
            self.fail(error=err)
        self.assertEqual(c.firings(self.feed(), c.DEFAULT_THRESHOLDS), [])

    def test_substantial_turn_by_files(self):
        self.hook("UserPromptSubmit", prompt="refactor")
        for f in ("a.py", "b.py", "c.py"):
            self.hook("PostToolUse", tool_name="Write", tool_input={"file_path": f, "content": "x"})
        self.hook("Stop")
        self.hook("UserPromptSubmit", prompt="small one")
        self.hook("PostToolUse", tool_name="Edit", tool_input={"file_path": "a.py", "old_string": "x", "new_string": "y"})
        self.hook("Stop")
        fs = c.firings(self.feed(), c.DEFAULT_THRESHOLDS)
        self.assertEqual([f["signal"] for f in fs], ["turn_end_substantial"])

    def test_idle_after_activity(self):
        self.hook("UserPromptSubmit", prompt="x")
        fs = c.idle_firings(self.feed(), c.DEFAULT_THRESHOLDS, until=self.t[0] + 21 * 60)
        self.assertEqual([f["signal"] for f in fs], ["idle_dirty"])
        self.assertEqual(c.idle_firings(self.feed(), c.DEFAULT_THRESHOLDS, until=self.t[0] + 60), [])


class TestLensRules(CodepanionCase):
    def test_the_shipped_examples_pass(self):
        self.configure(lenses=("stuck", "unfinished", "big-turn"))
        c.write_json(c.config_file(self.ws), {"lenses": ["stuck", "unfinished", "big-turn"]})
        self.assertEqual(c.check(self.ws)[0], [])

    def test_bad_lenses_are_refused(self):
        self.configure(lenses=())
        bad = {
            "nowake": "---\nname: nowake\nwakes_on: []\nspeaks: finding\n---\n## Rubric\n1. x\n## Say it like this\n- a\n## Never like this\n- b\n- c\n",
            "fewbad": "---\nname: fewbad\nwakes_on: [stuck]\nspeaks: finding\n---\n## Rubric\n1. x\n## Say it like this\n- a\n## Never like this\n- b\n",
            "noquestion": "---\nname: noquestion\nwakes_on: [stuck]\nspeaks: question\n---\n## Rubric\n1. x\n## Say it like this\n- an answer\n## Never like this\n- b\n- c\n",
            "norubric": "---\nname: norubric\nwakes_on: [stuck]\nspeaks: finding\n---\n## Rubric\nwhenever\n## Say it like this\n- a\n## Never like this\n- b\n- c\n",
        }
        for name, text in bad.items():
            (c.lenses_dir(self.ws) / f"{name}.md").write_text(text)
            c.write_json(c.config_file(self.ws), {"lenses": [name]})
            self.assertTrue(c.check(self.ws)[0], name)

    def test_at_most_three_active_lenses(self):
        self.configure(lenses=("stuck", "unfinished", "big-turn"))
        shutil.copy(EXAMPLES / "stuck.md", c.lenses_dir(self.ws) / "stuck2.md")
        text = (c.lenses_dir(self.ws) / "stuck2.md").read_text().replace("name: stuck", "name: stuck2")
        (c.lenses_dir(self.ws) / "stuck2.md").write_text(text)
        c.write_json(c.config_file(self.ws), {"lenses": ["stuck", "unfinished", "big-turn", "stuck2"]})
        self.assertTrue(any("at most 3" in p for p in c.check(self.ws)[0]))


class TestEvents(CodepanionCase):
    def test_a_stuck_session_wakes_the_lens_once(self):
        self.configure()
        self.hook("UserPromptSubmit", prompt="tests")
        for _ in range(3):
            self.fail()
        evs = c.events()
        self.assertEqual(len(evs), 1)
        self.assertEqual((evs[0]["source"], evs[0]["scope"], evs[0]["match"]), ("codepanion", "duck", "stuck"))
        self.assertNotIn("tests", json.dumps(evs))  # no prompt text leaves the module
        self.fail()
        self.assertEqual(c.events(), [])

    def test_nothing_wakes_without_a_lens_for_it(self):
        self.configure(lenses=("unfinished",))
        for _ in range(3):
            self.fail()
        self.assertEqual(c.events(), [])

    def test_a_codepanion_that_fails_check_never_runs(self):
        self.configure(lenses=("missing",))
        for _ in range(3):
            self.fail()
        self.assertEqual(c.events(), [])

    def test_old_signals_are_recorded_but_wake_nobody(self):
        self.configure()
        for _ in range(3):
            self.fail()
        self.t[0] += c.FRESH_SECONDS + 5
        self.assertEqual(c.events(), [])

    def test_the_daemon_routes_codepanion_signals(self):
        spaces = [(self.ws, {"triggers": {"watch": {"on": "codepanion:stuck", "task": "react"}}, "routines": {}})]
        ev = {"source": "codepanion", "scope": "duck", "match": "stuck", "what": "the signal stuck"}
        old = ta.module_events
        ta.module_events = lambda: {"codepanion": 10}
        self.addCleanup(setattr, ta, "module_events", old)
        d = ta.Daemon(runner=lambda cmd: None, events=lambda m: [], clock=lambda: 0)
        d.route(ev, spaces, 0)
        d.route(dict(ev, match="idle_dirty"), spaces, 0)
        self.assertEqual([p["what"] for p in d.pending.values()], [["the signal stuck"]])


    def test_the_codepanion_is_polled_without_any_trigger(self):
        self.assertIn("codepanion", ta.always_polled())
        for k, v in {"WORKSPACES": self.wsdir, "STATE_HOME": self.tmp / "automation"}.items():
            old = getattr(ta, k)
            setattr(ta, k, v)
            self.addCleanup(setattr, ta, k, old)
        polled = []
        d = ta.Daemon(runner=lambda cmd: None, events=lambda m: polled.append(m) or [], clock=lambda: 1000)
        d.tick()
        self.assertIn("codepanion", polled)

    def test_one_daemon_at_a_time(self):
        for k, v in {"STATE_HOME": self.tmp / "automation", "LOCK_FILE": self.tmp / "automation" / "daemon.lock"}.items():
            old = getattr(ta, k)
            setattr(ta, k, v)
            self.addCleanup(setattr, ta, k, old)
        self.assertIsNone(ta.daemon_pid())
        lock = ta.hold_lock()
        self.assertEqual(ta.daemon_pid(), os.getpid())
        self.assertIsNone(ta.hold_lock())  # a second daemon would start every run twice
        lock.close()
        self.assertIsNone(ta.daemon_pid())

class TestTools(CodepanionCase):
    def test_digest_carries_the_lenses_and_hides_other_scopes(self):
        self.configure()
        self.hook("UserPromptSubmit", prompt="why does login fail")
        self.fail()
        text = out_of(c.digest, "duck", "s1", "stuck", 60)
        self.assertIn("=== lens stuck ===", text)
        self.assertIn("KeyError", text)
        c.watch_set(str(self.other), "someone-else")
        self.hook("UserPromptSubmit", cwd=self.other, session="s2", prompt="secret plans")
        with self.assertRaises(c.ToolError):
            c.digest("duck", "s2", None, 60)

    def test_note_rules(self):
        self.configure()
        self.hook("UserPromptSubmit", prompt="x")
        with self.assertRaisesRegex(c.ToolError, "question"):
            c.note("duck", "stuck", "Add a try/except.", "14:02 Bash failed 3 times", "s1")
        with self.assertRaisesRegex(c.ToolError, "evidence"):
            c.note("duck", "stuck", "What is the key?", "", "s1")
        with self.assertRaisesRegex(c.ToolError, "not an active lens"):
            c.note("duck", "reviewer", "What is the key?", "14:02 Bash failed", "s1")
        out_of(c.note, "duck", "stuck", "What is the key when it fails?", "14:02 Bash failed 3 times", "s1")
        with self.assertRaisesRegex(c.ToolError, "already said"):
            c.note("duck", "stuck", "What is the key when it fails?", "14:02 Bash failed 3 times", "s1")
        self.assertEqual(c.unseen(), 1)
        self.assertEqual(len(c.mark_seen("duck")), 1)
        self.assertEqual(c.unseen(), 0)

    def test_note_budget(self):
        self.configure(budget={"runs_per_hour": 6, "notes_per_hour": 1})
        out_of(c.note, "duck", "stuck", "First question?", "14:02 Bash failed", None)
        with self.assertRaisesRegex(c.ToolError, "budget"):
            c.note("duck", "stuck", "Second question?", "14:05 Bash failed", None)

    def test_diff_reads_without_running_repo_programs(self):
        if not shutil.which("git"):
            self.skipTest("needs git")
        run = lambda *a: subprocess.run(["git", "-C", str(self.project), *a], check=True, capture_output=True)  # noqa: E731
        run("init", "-q")
        (self.project / "a.txt").write_text("one\n")
        run("add", "a.txt")
        run("-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "core.hooksPath=/dev/null", "commit", "-qm", "i")
        marker = self.tmp / "ran"
        run("config", "diff.external", f"sh -c 'touch {marker}'")
        run("config", "filter.evil.clean", f"sh -c 'touch {marker}; cat'")
        (self.project / ".gitattributes").write_text("*.txt filter=evil diff=evil\n")
        run("config", "diff.evil.textconv", f"sh -c 'touch {marker}; cat'")
        (self.project / "a.txt").write_text("two\n")
        self.hook("UserPromptSubmit", prompt="x")
        text = out_of(c.diff, "duck", None, "diff", 100)
        self.assertIn("+two", text)
        self.assertFalse(marker.exists(), "git ran a program from the repository's config")

    def test_git_runs_where_the_session_started(self):
        sub = self.project / "src"
        self.hook("SessionStart", cwd=sub, source="startup")
        self.assertEqual(c.repo_of(self.feed()), str(sub))
        self.hook("SessionStart", cwd=self.other, session="s3", source="startup")
        self.assertFalse((c.feed_dir() / "s3.jsonl").exists())

    def test_diff_refuses_in_a_backtest(self):
        os.environ["TANKA_CODEPANION_BACKTEST"] = "1"
        self.addCleanup(os.environ.pop, "TANKA_CODEPANION_BACKTEST", None)
        with self.assertRaisesRegex(c.ToolError, "backtest"):
            c.diff("duck", None, "stat", 50)


class TestBacktest(CodepanionCase):
    def transcript(self, session="past1"):
        d = c.claude_project_dir(str(self.project))
        d.mkdir(parents=True)
        base = time.time() - 3600
        recs = [{"type": "user", "timestamp": base, "message": {"role": "user", "content": "make the tests pass"}}]
        for i in range(3):
            recs += [
                {"type": "assistant", "timestamp": base + 10 + i, "message": {"stop_reason": "tool_use", "content": [
                    {"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {"command": "pytest"}}]}},
                {"type": "user", "timestamp": base + 11 + i, "message": {"content": [
                    {"type": "tool_result", "tool_use_id": f"t{i}", "is_error": True, "content": "Exit code 1\nKeyError: 'id'"}]}},
            ]
        recs.append({"type": "assistant", "timestamp": base + 20, "message": {"stop_reason": "end_turn", "content": [{"type": "text", "text": "done"}]}})
        from datetime import datetime, timezone
        with (d / f"{session}.jsonl").open("w") as f:
            for r in recs:
                r["timestamp"] = datetime.fromtimestamp(r["timestamp"], timezone.utc).isoformat().replace("+00:00", "Z")
                f.write(json.dumps(r) + "\n")

    def test_transcripts_become_events(self):
        self.transcript()
        evs = c.transcript_events(c.claude_project_dir(str(self.project)) / "past1.jsonl", str(self.project))
        self.assertEqual([e["e"] for e in evs], ["session_start", "prompt", "tool", "tool", "tool", "turn_end"])
        self.assertEqual(c.firings(evs, c.DEFAULT_THRESHOLDS)[0]["signal"], "stuck")

    def test_backtest_lists_wakeups_and_sends_nothing(self):
        self.configure()
        self.transcript()
        text = out_of(c.backtest, self.ws, 14, 0)
        self.assertIn("1 wake-up(s)", text)
        self.assertIn("stuck", text)
        self.assertFalse((self.home / "notes").exists())

    def test_say_runs_the_model_in_a_sandbox(self):
        self.configure()
        self.transcript()
        seen = []

        def fake_run(cmd):
            seen.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="REPORT: -", stderr="")
        text = out_of(c.backtest, self.ws, 14, 1, fake_run)
        self.assertIn("(stays quiet)", text)
        self.assertEqual(seen[0][1], "run")
        self.assertFalse((self.home / "notes").exists())


class TestInstall(CodepanionCase):
    def test_module_passes_its_rules_and_installs(self):
        self.assertEqual(tm.check("codepanion"), [])
        with contextlib.redirect_stdout(io.StringIO()):
            old = os.environ.get("TANKA_CODEPANION_HOME")
            os.environ["TANKA_CODEPANION_HOME"] = str(self.home)
            try:
                self.assertEqual(tm.install("codepanion", self.ws, "duck"), 0)
            finally:
                os.environ.pop("TANKA_CODEPANION_HOME") if old is None else os.environ.__setitem__("TANKA_CODEPANION_HOME", old)
        tool = (self.ws / ".claude" / "skills" / "codepanion" / "tools" / "codepanion_note.py").read_text()
        self.assertIn('SCOPE = "duck"', tool)
        self.assertTrue(c.config_file(self.ws).is_file())
        self.assertEqual(json.loads(c.config_file(self.ws).read_text())["lenses"], [])


class TestCards(CodepanionCase):
    def test_check_items_group_by_topic_and_never_duplicate(self):
        a = c.add_card("duck", "check", "app", "Agregar tests al login")
        b = c.add_card("duck", "check", "APP", "agregar  tests al login")
        self.assertEqual(a["id"], b["id"])
        self.assertTrue(b["existed"])
        self.assertEqual(a["topic"], "app")  # the watched project's folder name
        c.add_card("duck", "check", "facturas", "Pagar la luz")
        checks = c.pending("duck")["checks"]
        self.assertEqual(sorted(x["topic"] for x in checks), ["app", "facturas"])
        self.assertEqual(next(x for x in checks if x["topic"] == "app")["project"], str(self.project))

    def test_reminder_times(self):
        self.t[0] = datetime(2026, 10, 1, 12, 0).timestamp()
        self.assertEqual(datetime.fromtimestamp(c.parse_at("17:30", self.t[0])), datetime(2026, 10, 1, 17, 30))
        self.assertEqual(datetime.fromtimestamp(c.parse_at("09:00", self.t[0])), datetime(2026, 10, 2, 9, 0))
        with self.assertRaisesRegex(c.ToolError, "already past"):
            c.parse_at("2026-09-30 10:00", self.t[0])
        with self.assertRaisesRegex(c.ToolError, "at most"):
            c.parse_at("2027-09-30 10:00", self.t[0])
        with self.assertRaisesRegex(c.ToolError, "needs at"):
            c.add_card("duck", "reminder", None, "Llamar a soporte")
        with self.assertRaisesRegex(c.ToolError, "no time"):
            c.add_card("duck", "check", None, "Llamar a soporte", "17:30")

    def test_closing_by_id_or_text(self):
        i = c.add_card("duck", "check", "app", "Agregar tests al login")
        c.add_card("duck", "check", "web", "Agregar tests al login")
        with self.assertRaisesRegex(c.ToolError, "2 open"):
            c.close_card("duck", "check", None, "Agregar tests al login")
        self.assertEqual(c.close_card("duck", "check", None, i["id"])["id"], i["id"])
        r = c.add_card("duck", "reminder", None, "Llamar a soporte", "23:59")
        self.assertIsNotNone(c.close_card("duck", "reminder", None, "llamar a soporte")["done_at"])
        with self.assertRaisesRegex(c.ToolError, "No open"):
            c.close_card("duck", "reminder", None, r["id"])

    def test_only_lens_findings_are_rationed(self):
        self.configure(budget={"runs_per_hour": 6, "notes_per_hour": 1})
        c.add_card("duck", "check", "app", "Tests del login", by="codepanion", evidence="16:40 prompt: lo hago mañana")
        with self.assertRaisesRegex(c.ToolError, "budget"):
            c.add_card("duck", "check", "app", "Docs del login", by="codepanion", evidence="16:41 prompt: después")
        c.add_card("duck", "reminder", None, "Pedido del usuario", "23:59", by="codepanion")  # asked in chat: no evidence

    def test_due_reminders_notify_once_and_count_in_the_status_line(self):
        sent = []
        old = c.tc.desktop_notify
        c.tc.desktop_notify = lambda title, text: sent.append((title, text)) or True
        self.addCleanup(setattr, c.tc, "desktop_notify", old)
        r = c.add_card("duck", "reminder", "app", "Revisar el PR", datetime.fromtimestamp(self.t[0] + 120).strftime("%H:%M"))
        self.assertEqual(c.fire_reminders(), 0)
        self.t[0] = r["at"] + 1
        self.assertEqual(c.fire_reminders(), 1)
        self.assertEqual(c.fire_reminders(), 0)
        self.assertEqual(sent, [("⏰ app · duck", "Revisar el PR")])
        self.assertEqual(c.due_count(), 1)
        c.update_card("duck", r["id"], "snooze", 10)
        self.assertEqual(c.due_count(), 0)
        self.t[0] += 11 * 60
        self.assertEqual(c.fire_reminders(), 1)
        c.update_card("duck", r["id"], "done")
        self.assertEqual(c.due_count(), 0)

    def test_cards_belong_to_one_workspace(self):
        (self.wsdir / "other" / ".tanka").mkdir(parents=True)
        c.add_card("duck", "check", "app", "Solo de duck")
        self.assertEqual(c.pending("other")["checks"], [])
        self.assertIn("Solo de duck", out_of(c.list_pending, "duck", None))
        self.assertIn("Nothing pending", out_of(c.list_pending, "other", None))

    def test_the_tool_adds_and_ticks(self):
        self.assertIn("Added", out_of(c.card_tool, "duck", "check", "Pagar la luz", "casa", None, False, ""))
        self.assertIn("Already there", out_of(c.card_tool, "duck", "check", "Pagar la luz", "casa", None, False, ""))
        self.assertIn("Ticked", out_of(c.card_tool, "duck", "check", "pagar la luz", None, None, True, ""))
        self.assertIn("done", out_of(c.list_pending, "duck", "casa"))


class UICase(CodepanionCase):
    """A page server on a free port, with a note and a prompt that carries markup."""

    def setUp(self):
        super().setUp()
        self.configure()
        self.hook("UserPromptSubmit", prompt="<img src=x onerror=alert(1)> fix the login")
        out_of(c.note, "duck", "stuck", "What does the key look like when it fails?", "14:02 Bash failed 3 times", "s1")
        self.srv = ui.make_server()
        self.port = self.srv.server_address[1]
        self.token = self.srv.RequestHandlerClass.token
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)

    def call(self, method, path, body=None, host=None, token=True, ctype="application/json"):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Host": host or f"127.0.0.1:{self.port}"}
        if token:
            headers["X-Codepanion-Token"] = self.token
        if body is not None:
            headers["Content-Type"] = ctype
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = conn.getresponse()
        data = r.read()
        conn.close()
        return r.status, data, r


class TestUI(UICase):
    def test_a_foreign_host_is_refused(self):
        status, _, _ = self.call("GET", f"/?t={self.token}", host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)
        status, _, _ = self.call("GET", "/api/state", host=f"evil.example:{self.port}")
        self.assertEqual(status, 403)

    def test_the_token_is_required(self):
        self.assertEqual(self.call("GET", "/")[0], 403)
        self.assertEqual(self.call("GET", "/?t=wrong")[0], 403)
        self.assertEqual(self.call("GET", "/api/state", token=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/rate", {"id": "x", "verdict": "good"}, token=False)[0], 403)

    def test_no_cross_origin_preflight_is_granted(self):
        status, _, r = self.call("OPTIONS", "/api/rate", token=False)
        self.assertNotEqual(status, 200)
        self.assertIsNone(r.getheader("Access-Control-Allow-Origin"))

    def test_the_page_has_a_strict_policy_and_no_html_sinks(self):
        status, page, r = self.call("GET", f"/?t={self.token}", token=False)
        self.assertEqual(status, 200)
        csp = r.getheader("Content-Security-Policy")
        self.assertIn("default-src 'none'", csp)
        self.assertIn("script-src 'nonce-", csp)
        text = page.decode()
        self.assertNotIn("__NONCE__", text)
        for sink in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
            self.assertNotIn(sink, text)

    def test_state_shows_notes_sessions_and_lenses(self):
        status, data, _ = self.call("GET", "/api/state")
        self.assertEqual(status, 200)
        duck = next(s for s in json.loads(data)["scopes"] if s["scope"] == "duck")
        self.assertEqual(len(duck["notes"]), 1)
        self.assertEqual(duck["sessions"][0]["id"], "s1")
        self.assertEqual(duck["lenses"][0]["name"], "stuck")
        self.assertEqual(duck["problems"], [])

    def test_session_detail_has_the_timeline_and_firings(self):
        for _ in range(3):
            self.fail()
        status, data, _ = self.call("GET", "/api/session?scope=duck&id=s1")
        self.assertEqual(status, 200)
        d = json.loads(data)
        self.assertEqual(d["events"][0]["e"], "prompt")
        self.assertEqual([f["signal"] for f in d["firings"]], ["stuck"])

    def test_rating_from_the_page_is_recorded(self):
        note_id = c.read_notes("duck")[0]["id"]
        status, _, _ = self.call("POST", "/api/rate", {"id": note_id, "verdict": "bad"})
        self.assertEqual(status, 200)
        self.assertEqual(c.verdicts(), {note_id: "bad"})
        self.assertTrue(c.read_notes("duck")[0]["seen"])
        self.assertEqual(self.call("POST", "/api/rate", {"id": note_id, "verdict": "meh"})[0], 400)
        self.assertEqual(self.call("POST", "/api/rate", {"id": note_id, "verdict": "good"}, ctype="text/plain")[0], 415)

    def test_the_page_ticks_cards_but_does_not_create_them(self):
        item = c.add_card("duck", "check", "app", "Pagar la luz")
        self.assertEqual(self.call("POST", "/api/card", {"scope": "duck", "kind": "check", "text": "x"})[0], 404)
        self.assertEqual(self.call("POST", "/api/item", {"scope": "duck", "id": item["id"], "done": True})[0], 200)
        self.assertIsNotNone(c.pending("duck")["checks"][0]["items"][0]["done_at"])
        rid = c.add_card("duck", "reminder", "", "Llamar", "23:59")["id"]
        self.assertEqual(self.call("POST", "/api/card-action", {"scope": "duck", "id": rid, "action": "archive"})[0], 200)
        self.assertEqual(c.pending("duck")["reminders"], [])
        self.assertEqual(self.call("POST", "/api/item", {"scope": "nobody", "id": item["id"], "done": False})[0], 400)
        status, data, _ = self.call("GET", "/api/state")
        self.assertIn("pending", next(s for s in json.loads(data)["scopes"] if s["scope"] == "duck"))


class TestChat(UICase):
    """The page's chat, with the model run replaced: what reaches `tanka run`, and what comes back."""

    def setUp(self):
        super().setUp()
        self.runs = []
        self.replies = []

        def fake(scope, text, extra):
            self.runs.append((scope, text, list(extra)))
            return self.replies.pop(0) if self.replies else (0, "Listo: te lo recuerdo a las 18:00.", "")
        original = chat.run_tanka
        chat.run_tanka = fake
        self.addCleanup(setattr, chat, "run_tanka", original)

    def say(self, text):
        status, data, _ = self.call("POST", "/api/chat", {"scope": "duck", "text": text})
        for _ in range(100):
            if not chat.busy("duck"):
                break
            time.sleep(0.02)
        return status, json.loads(data)

    def stream(self):
        data = json.loads(self.call("GET", "/api/state")[1])
        return next(s for s in data["scopes"] if s["scope"] == "duck")["chat"]

    def test_a_message_gets_an_answer_in_one_session_for_the_day(self):
        self.assertEqual(self.say("recuérdame a las 18:00 llamar")[0], 200)
        self.assertEqual(self.say("y mañana lo mismo")[0], 200)
        (_, text, first), (_, _, second) = self.runs
        self.assertEqual(text, "recuérdame a las 18:00 llamar")
        self.assertEqual(first[0], "--session-id")
        self.assertEqual(second, ["--resume", first[1]])
        whos = [m["who"] for m in self.stream()]
        self.assertEqual([w for w in whos if w in ("you", "tanka")], ["you", "tanka", "you", "tanka"])

    def test_the_codepanion_speaks_in_the_same_stream(self):
        c.add_card("duck", "reminder", "", "Pagar la luz", "23:59")
        with c.card_store("duck", write=True) as cards:
            cards[0]["at"] = time.time() - 1
        os.environ["TANKA_CODEPANION_BACKTEST"] = "1"  # no desktop notification from a test
        self.addCleanup(os.environ.pop, "TANKA_CODEPANION_BACKTEST", None)
        self.assertEqual(c.fire_reminders(), 1)
        whos = [m["who"] for m in self.stream()]
        self.assertIn("note", whos)
        self.assertIn("reminder", whos)

    def test_an_answer_shows_the_cards_it_made_and_ticked(self):
        old = c.add_card("duck", "check", "app", "Pagar la luz")

        def acting(scope, text, extra):
            self.runs.append((scope, text, list(extra)))
            self.t[0] = time.time()  # the cards are made while it answers
            c.add_card("duck", "reminder", "", "Llamar", "23:59", by="codepanion")
            c.close_card("duck", "check", "app", "Pagar la luz", by="codepanion")
            return 0, "Listo.", ""
        chat.run_tanka = acting
        self.say("recuérdame llamar y ya pagué la luz")
        reply = [m for m in self.stream() if m["who"] == "tanka"][-1]
        kinds = {(f["type"], f["text"]) for f in reply["effects"]}
        self.assertEqual(kinds, {("reminder", "Llamar"), ("done", "Pagar la luz")})
        self.assertEqual(next(f["id"] for f in reply["effects"] if f["type"] == "done"), old["id"])

    def test_a_fired_reminder_stays_in_the_chat_with_what_became_of_it(self):
        r = c.add_card("duck", "reminder", "", "Pagar la luz", "23:59")
        with c.card_store("duck", write=True) as cards:
            next(x for x in cards if x["id"] == r["id"])["at"] = time.time() - 1
        os.environ["TANKA_CODEPANION_BACKTEST"] = "1"
        self.addCleanup(os.environ.pop, "TANKA_CODEPANION_BACKTEST", None)
        c.fire_reminders()
        event = next(m for m in self.stream() if m["who"] == "reminder")
        self.assertEqual((event["state"], event["text"]), ("due", "Pagar la luz"))
        c.update_card("duck", r["id"], "snooze", 10)
        self.assertEqual([m["state"] for m in self.stream() if m["who"] == "reminder"], ["snoozed"])
        c.update_card("duck", r["id"], "done", 0)
        self.assertEqual([m["state"] for m in self.stream() if m["who"] == "reminder"], ["done"])

    def test_a_reply_reads_what_the_codepanion_said_on_its_own(self):
        self.say("hola")
        r = c.add_card("duck", "reminder", "", "Tomar agua", "23:59")
        with c.card_store("duck", write=True) as cards:
            next(x for x in cards if x["id"] == r["id"])["at"] = time.time() - 1
        os.environ["TANKA_CODEPANION_BACKTEST"] = "1"
        self.addCleanup(os.environ.pop, "TANKA_CODEPANION_BACKTEST", None)
        self.t[0] = time.time()  # it fires after "hola"
        c.fire_reminders()
        self.say("listo")
        sent = self.runs[-1][1]
        self.assertIn(r["id"], sent)
        self.assertIn("Tomar agua", sent)
        self.assertTrue(sent.endswith("The user's message:\nlisto"))
        self.say("gracias")
        self.assertEqual(self.runs[-1][1], "gracias")  # nothing new since: it goes as typed

    def test_a_new_day_opens_with_the_end_of_the_earlier_chat(self):
        yesterday = time.time() - 86400
        c.chat_event("duck", {"t": yesterday, "who": "you", "text": "el informe va el viernes"})
        c.chat_event("duck", {"t": yesterday + 5, "who": "tanka", "text": "Anotado."})
        self.say("y qué quedó de ayer?")
        sent, extra = self.runs[-1][1], self.runs[-1][2]
        self.assertEqual(extra[0], "--session-id")
        self.assertIn("user: el informe va el viernes", sent)
        self.assertIn("you: Anotado.", sent)
        self.say("ok")
        self.assertEqual(self.runs[-1][1], "ok")  # resumed: the session already has it

    def test_routine_reports_show_in_the_chat_but_lens_runs_do_not(self):
        f = self.ws / ".tanka" / "reports.jsonl"
        f.write_text(json.dumps({"t": time.time(), "kind": "routine", "name": "inbox", "on": None, "exit": 0, "report": "2 correos nuevos"}) + "\n"
                     + json.dumps({"t": time.time(), "kind": "trigger", "name": "watch", "on": "codepanion:*", "exit": 0, "report": "nota"}) + "\n")
        shown = [m for m in self.stream() if m["who"] == "report"]
        self.assertEqual([(m["name"], m["text"], m["failed"]) for m in shown], [("inbox", "2 correos nuevos", False)])

    def test_a_failed_answer_offers_the_message_again(self):
        self.replies = [(1, "", "Error: Reached max turns (10)")]
        self.say("haz algo largo")
        err = [m for m in self.stream() if m["who"] == "error"][-1]
        self.assertEqual((err["code"], err["retry"]), ("noanswer", "haz algo largo"))

    def test_bad_messages_are_refused(self):
        self.assertEqual(self.say("   ")[0], 400)
        self.assertEqual(self.say("x" * 1001)[0], 400)
        self.assertEqual(self.call("POST", "/api/chat", {"scope": "duck", "text": "hola"}, token=False)[0], 403)
        self.assertEqual(self.call("POST", "/api/chat", {"scope": "nobody", "text": "hola"})[0], 400)
        chat._busy["duck"] = time.time()
        self.addCleanup(chat._busy.pop, "duck", None)
        status, data, _ = self.call("POST", "/api/chat", {"scope": "duck", "text": "hola"})
        self.assertEqual(status, 400)
        self.assertIn("still answering", json.loads(data)["error"])
        self.assertEqual(self.runs, [])

    def test_a_failed_run_says_so_and_a_lost_session_starts_again(self):
        self.replies = [(1, "", "Error: Reached max turns (10)")]
        self.say("haz algo largo")
        last = self.stream()[-1]
        self.assertEqual(last["who"], "error")
        self.assertIn("max turns", last["text"])
        self.assertFalse(chat.session_file("duck").exists())  # nothing was kept, so the next one starts fresh
        self.say("hola")
        self.replies = [(1, "", "No conversation found with session ID"), (0, "Hola de nuevo.", "")]
        self.say("sigues ahí?")
        resumed, again = self.runs[-2][2], self.runs[-1][2]
        self.assertEqual(resumed[0], "--resume")
        self.assertEqual(again[0], "--session-id")
        self.assertNotEqual(again[1], resumed[1])
        self.assertEqual(self.stream()[-1]["text"], "Hola de nuevo.")



class TestBrief(CodepanionCase):
    """The daily brief: once a day at its time, with what the day holds and what has stalled. No model."""

    def setUp(self):
        super().setUp()
        os.environ["TANKA_CODEPANION_BACKTEST"] = "1"
        self.addCleanup(os.environ.pop, "TANKA_CODEPANION_BACKTEST", None)
        self.morning = datetime.now().replace(hour=8, minute=0, second=0, microsecond=0).timestamp()
        self.t[0] = self.morning - 5 * 86400

    def briefs(self):
        return [m for m in c.read_feed(c.chat_file("duck")) if m["who"] == "brief"]

    def test_the_brief_comes_once_at_its_time_with_the_stalled_items(self):
        old = c.add_card("duck", "check", "app", "Revisar el PR")
        self.t[0] = self.morning
        fresh = c.add_card("duck", "check", "app", "Subir la versión")
        rem = c.add_card("duck", "reminder", "", "Llamar a Ana", "17:00")
        self.assertEqual(c.daily_brief(), 0)  # 08:00, before the default 09:00
        self.t[0] = self.morning + 3600
        self.assertEqual(c.daily_brief(), 1)
        self.assertEqual(c.daily_brief(), 0)  # once a day
        b = self.briefs()[0]
        self.assertEqual((b["reminders"], b["topics"], b["stale"]), ([rem["id"]], {"app": 2}, [old["id"]]))
        self.assertNotIn(fresh["id"], b["stale"])

    def test_the_chat_shows_the_brief_as_things_stand(self):
        old = c.add_card("duck", "check", "app", "Revisar el PR")
        self.t[0] = self.morning + 3600
        c.daily_brief()
        c.close_card("duck", "check", "app", "Revisar el PR")
        with c.card_store("duck") as cards:
            shown = next(m for m in chat.stream("duck", [], list(cards)) if m["who"] == "brief")
        self.assertEqual([(i["id"], i["done"]) for i in shown["stale"]], [(old["id"], True)])

    def test_no_brief_with_nothing_open_when_off_or_too_late(self):
        self.t[0] = self.morning + 3600
        c.add_card("duck", "check", "app", "algo")
        c.write_json(c.config_file(self.ws), {"brief": ""})
        self.assertEqual(c.daily_brief(), 0)
        c.write_json(c.config_file(self.ws), {"brief": "09:00"})
        self.t[0] = self.morning + 14 * 3600  # 22:00: more than 12 h late, the day is skipped
        self.assertEqual(c.daily_brief(), 0)
        self.assertEqual(self.briefs(), [])

    def test_the_cli_sets_the_time_and_check_refuses_a_bad_one(self):
        self.assertEqual(cli.main(["brief", str(self.ws), "07:30", "--stale", "5"]), 0)
        cfg = c.load_config(self.ws)
        self.assertEqual((cfg["brief"], cfg["stale_days"]), ("07:30", 5))
        self.assertEqual(cli.main(["brief", str(self.ws), "25:00"]), 1)
        c.write_json(c.config_file(self.ws), {"brief": "9am"})
        self.assertTrue(any("brief must be" in p for p in c.check(self.ws)[0]))


class TestStreaming(CodepanionCase):
    """The chat's run as it streams: the draft the page shows, and the answer at the end."""

    def fake_bin(self, lines, code=0):
        script = self.tmp / "tanka"
        out = "\n".join(json.dumps(x) for x in lines)
        script.write_text(f"#!/bin/sh\ncat <<'EOF'\n{out}\nEOF\nexit {code}\n")
        script.chmod(0o755)
        old = chat.TANKA_BIN
        chat.TANKA_BIN = script
        self.addCleanup(setattr, chat, "TANKA_BIN", old)

    def test_the_answer_comes_from_the_result_and_the_draft_follows_the_text(self):
        self.fake_bin([{"type": "stream_event", "event": {"type": "message_start"}},
                       {"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Lis"}}},
                       {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__tanka__codepanion_card"}]}},
                       {"type": "result", "result": "Listo: a las 18:00.", "is_error": False}])
        self.assertEqual(chat.run_tanka("duck", "hola", []), (0, "Listo: a las 18:00.", ""))
        self.assertEqual(chat._drafts.pop("duck"), {"text": "Lis", "tool": "mcp__tanka__codepanion_card"})

    def test_a_run_that_ends_in_error_has_no_answer(self):
        self.fake_bin([{"type": "result", "subtype": "error_max_turns", "is_error": True}], code=1)
        code, out, err = chat.run_tanka("duck", "hola", [])
        chat._drafts.pop("duck", None)
        self.assertEqual((code, out, err), (1, "", "error_max_turns"))




class TestPageName(unittest.TestCase):
    def test_the_page_uses_the_persona_name(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        ws = tmp / "duck"
        (ws / ".tanka").mkdir(parents=True)
        self.assertEqual(c.persona_name(ws), "duck")
        (ws / ".tanka" / "persona.json").write_text(json.dumps({"name": "Nova"}))
        self.assertEqual(c.persona_name(ws), "Nova")


class TestBuiltins(CodepanionCase):
    """The guardian and the session close: no lens, no model, from the first poll."""

    def git(self, *args, email="me@example.com"):
        env = dict(os.environ, GIT_AUTHOR_NAME="Me", GIT_AUTHOR_EMAIL=email, GIT_COMMITTER_NAME="Me",
                   GIT_COMMITTER_EMAIL=email, GIT_CONFIG_NOSYSTEM="1", HOME=str(self.tmp))
        subprocess.run(["git", "-C", str(self.project), *args], check=True, capture_output=True, env=env)

    def commit(self, name, text, email="me@example.com"):
        (self.project / name).write_text(text)
        self.git("add", name)
        self.git("commit", "-q", "-m", f"add {name}", email=email)

    def setUp(self):
        super().setUp()
        self.git("init", "-q")
        c.write_json(c.config_file(self.ws), {"lenses": [], "proactivity": 1})
        (self.ws / ".tanka" / "persona.json").write_text(json.dumps({"name": "Nova", "language": "es"}))
        self.notified = []
        old = c.tc.desktop_notify
        c.tc.desktop_notify = lambda title, text: self.notified.append((title, text)) or True
        self.addCleanup(setattr, c.tc, "desktop_notify", old)

    def bash(self, cmd, ok=True):
        self.hook("PostToolUse", tool_name="Bash", tool_input={"command": cmd}, tool_response={"is_error": not ok})

    def notes(self):
        return [n for n in c.read_notes("duck") if n["lens"] == "guardian"]

    def test_force_push_is_called_out_once_in_the_persona_language(self):
        self.hook("SessionStart")
        self.bash("git push --force origin main")
        c.events()
        c.events()
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        self.assertIn("push forzado", notes[0]["text"])
        self.assertEqual(self.notified[0][0], "Nova · guardian")

    def test_force_push_late_in_a_long_command_is_still_seen(self):
        self.hook("SessionStart")
        self.bash("cd /some/rather/long/path/to/a/checkout && " + "echo preparing the release notes; " * 6
                  + "git -C work push -q --force origin HEAD:main 2>&1")
        c.events()
        notes = self.notes()
        self.assertEqual(len(notes), 1)
        self.assertIn("push -q --force origin HEAD:main", notes[0]["text"])

    def test_code_that_names_a_secret_is_not_one(self):
        self.commit("README.md", "hello\n")
        self.hook("SessionStart")
        self.commit("settings.py", 'token = os.environ["TOKEN"]\napi_key = settings.API_KEY\n'
                                   'MSG = "looks like a secret: {where}."\n')
        self.bash("git commit -m settings")
        c.events()
        self.assertEqual(self.notes(), [])

    def test_plain_push_and_lease_free_commands_say_nothing(self):
        self.hook("SessionStart")
        self.bash("git push origin main")
        self.bash("git status --short")
        c.events()
        self.assertEqual(self.notes(), [])

    def test_secret_in_a_commit_names_the_place_not_the_secret(self):
        self.commit("README.md", "hello\n")
        self.hook("SessionStart")
        self.commit("config.py", f"KEY = '{FAKE_KEY}'\n")
        self.bash("git commit -m 'add config'")
        c.events()
        text = self.notes()[0]["text"]
        self.assertIn("config.py:1", text)
        self.assertNotIn(FAKE_KEY, text)

    def test_git_commit_that_made_no_commit_here_is_ignored(self):
        self.commit("config.py", f"KEY = '{FAKE_KEY}'\n")  # an old commit with a secret already in HEAD
        self.hook("SessionStart")
        self.t[0] += 3600
        self.bash("git -C ../elsewhere commit -m x")
        self.bash("python3 - <<'EOF'\nprint('git commit -m settings')\nEOF")
        c.events()
        self.assertEqual(self.notes(), [])

    def test_a_commit_under_an_unusual_identity(self):
        for i in range(6):
            self.commit(f"f{i}.txt", f"{i}\n")
        self.hook("SessionStart")
        self.commit("work.txt", "x\n", email="me@work.example")
        self.bash("git commit -m work")
        c.events()
        text = self.notes()[0]["text"]
        self.assertIn("me@work.example", text)
        self.assertIn("me@example.com", text)

    def test_guardian_can_be_turned_off(self):
        c.write_json(c.config_file(self.ws), {"lenses": [], "proactivity": 1, "guardian": False})
        self.hook("SessionStart")
        self.bash("git push -f")
        c.events()
        self.assertEqual(self.notes(), [])

    def test_the_session_close_puts_leftovers_on_the_project_card(self):
        self.commit("README.md", "hello\n")
        self.hook("SessionStart")
        self.hook("PostToolUse", tool_name="TodoWrite", tool_input={"todos": [
            {"content": "Write the migration", "status": "completed"},
            {"content": "Run the slow tests", "status": "in_progress"}]}, tool_response={})
        (self.project / "half.py").write_text("x = 1\n")
        self.hook("SessionEnd", reason="exit")
        c.events()
        p = c.pending("duck")
        items = [i["text"] for cd in p["checks"] for i in cd["items"]]
        self.assertTrue(any("half.py" in t for t in items), items)
        self.assertTrue(any("Run the slow tests" in t for t in items), items)
        self.assertFalse(any("Write the migration" in t for t in items), items)
        c.events()
        self.assertEqual(len([i for cd in c.pending("duck")["checks"] for i in cd["items"]]), len(items))

    def test_an_old_session_is_marked_closed_without_cards(self):
        self.commit("README.md", "hello\n")
        self.hook("SessionStart")
        (self.project / "old.py").write_text("x\n")
        self.hook("SessionEnd", reason="exit")
        self.t[0] += c.CLOSE_FRESH_HOURS * 3600 + 60
        c.events()
        self.assertEqual(c.pending("duck")["checks"], [])

    def test_a_session_closed_signal_exists_for_lenses(self):
        self.assertIn("session_closed", c.SIGNALS)
        evs = [{"t": 0, "e": "session_start"}, {"t": 10, "e": "turn_end"}]
        self.assertEqual(c.close_firings(evs, until=20), [])
        self.assertEqual(c.close_firings(evs, until=10 + c.CLOSE_IDLE_HOURS * 3600)[0]["signal"], "session_closed")


class TestTuning(CodepanionCase):
    def bad(self, text):
        if not self.feed():
            self.hook("UserPromptSubmit", prompt="fix the login")
        out_of(c.note, "duck", "stuck", text, "14:02 Bash failed 3 times", "s1")
        n = [x for x in c.read_notes("duck") if x["text"] == text][0]
        c.rate(n["id"], "bad")

    def test_two_bad_in_a_row_propose_once(self):
        self.configure()
        self.bad("What does the key look like?")
        self.assertEqual(c.suggestions("duck"), [])
        self.bad("Which test reproduces it?")
        self.bad("Did the fixture change?")
        (s,) = c.suggestions("duck")
        self.assertEqual((s["lens"], s["state"], s["tunable"]), ("stuck", "open", True))

    def test_stricter_raises_the_threshold_and_resolves(self):
        self.configure()
        self.bad("What does the key look like?")
        self.bad("Which test reproduces it?")
        before = c.load_config(self.ws)["thresholds"]["stuck"]
        out = c.lens_action("duck", "stuck", "stricter")
        after = c.load_config(self.ws)["thresholds"]["stuck"]
        self.assertGreater(after, before)
        self.assertIn(f"stuck {after}", out)
        self.assertEqual(c.suggestions("duck")[0]["state"], "stricter")

    def test_pause_and_resume(self):
        self.configure()
        c.lens_action("duck", "stuck", "pause")
        self.assertEqual(c.load_config(self.ws)["lenses"], [])
        self.assertTrue((c.lenses_dir(self.ws) / "stuck.md").is_file())
        c.lens_action("duck", "stuck", "resume")
        self.assertEqual(c.load_config(self.ws)["lenses"], ["stuck"])

    def test_guardian_pauses_but_has_no_threshold(self):
        self.configure()
        c.lens_action("duck", "guardian", "pause")
        self.assertFalse(c.load_config(self.ws)["guardian"])
        with self.assertRaisesRegex(c.ToolError, "no threshold"):
            c.lens_action("duck", "guardian", "stricter")

    def test_no_lens_is_a_warning(self):
        c.write_json(c.config_file(self.ws), {"lenses": []})
        problems, warnings = c.check(self.ws)
        self.assertEqual(problems, [])
        self.assertTrue(any("built-in checks" in w for w in warnings))


class TestSetup(CodepanionCase):
    def setUp(self):
        super().setUp()
        env = dict(os.environ)
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(env)))
        os.environ["TANKA_WORKSPACES"] = str(self.wsdir)
        self.pid = [None]
        self.started = []
        for mod, name, fn in ((cli, "SETTINGS", self.tmp / "claude" / "settings.json"),
                              (ta, "daemon_pid", lambda: self.pid[0]),
                              (ta, "start_detached", self.start),
                              (ta, "service", lambda *a, **k: self.fail_on("the service must not be installed")),
                              (c.tc, "desktop_notify", lambda title, text: self.started.append(title) or True)):
            old = getattr(mod, name)
            setattr(mod, name, fn)
            self.addCleanup(setattr, mod, name, old)

    def fail_on(self, why):
        raise AssertionError(why)

    def start(self):
        self.pid[0] = 4242
        return 4242

    def test_new_workspace_end_to_end_then_idempotent(self):
        out = out_of(cli.setup, ["nova", str(self.other), "--no-service"])
        ws = self.wsdir / "nova"
        self.assertTrue((ws / ".claude" / "skills" / "codepanion").is_dir())
        self.assertEqual(c.watch()[c.real(str(self.other))], "nova")
        self.assertIn("Stop", json.loads(cli.SETTINGS.read_text())["hooks"])
        self.assertEqual(ta.load(ws)["triggers"]["watch"]["on"], "codepanion:*")
        self.assertIn("ok the tap writes the feed", out)
        self.assertIn("ok the automation daemon (pid 4242)", out)
        self.assertEqual(self.started, ["Nova · ready"] if c.persona_name(ws) == "Nova" else [f"{c.persona_name(ws)} · ready"])
        self.assertEqual(list(c.feed_dir().glob("selftest-*")), [])  # the synthetic session is gone
        again = out_of(cli.setup, ["nova", str(self.other), "--no-service"])
        self.assertIn("= The codepanion module is already installed.", again)
        self.assertIn("= The trigger 'watch' already exists.", again)
        self.assertIn("= The tap is already installed", again)
        self.assertNotIn("Next", again)

    def test_bad_project(self):
        with self.assertRaisesRegex(c.ToolError, "is not a directory"):
            cli.setup(["nova", str(self.tmp / "nope")])


if __name__ == "__main__":
    unittest.main()
