"""The cauce module: the scope a workspace may use, what it asks cauce and how it reads the answer, the
tools as the assistant calls them, and the page's part. cauce itself is a fake that prints recorded
JSON and logs every call, so the tests need neither cauce nor a model."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import UTC, datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))
sys.path.insert(0, str(REPO / "modules" / "cauce"))
import cauce_link as cl  # noqa: E402
import tanka_kit as kit  # noqa: E402
import tanka_modules as tm  # noqa: E402
import tanka_tools as tt  # noqa: E402

FAKE = '''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_CAUCE_LOG"], "a") as fh:
    fh.write(json.dumps(args) + "\\n")
answers = json.load(open(os.environ["FAKE_CAUCE_ANSWERS"]))
if args[:1] == ["--version"]:
    print("cauce 0.1.0"); sys.exit(0)
key = args[0] if args[0] != "queue" else "queue"
if args[0] == "show":
    key = "show:" + args[1]
answer = answers.get(key)
if answer is None:
    print("no such thing", file=sys.stderr); sys.exit(1)
if isinstance(answer, dict) and "stderr" in answer:
    print(answer["stderr"], file=sys.stderr); sys.exit(2)
print(answer if isinstance(answer, str) else json.dumps(answer))
'''

NOW = datetime.now(UTC).isoformat(timespec="seconds")
FLOW = {"kind": "implement", "start": "sonnet/low", "ladder": ["sonnet/low", "sonnet/medium", "opus/medium"],
        "reasons": [], "steps": [{"seq": 1, "cell": "sonnet/low", "passed": 0, "failure": "code_bug",
                                  "move": "more_effort", "move_reason": "the work was shallow"}]}


def task(i, status, repo="github.com/o/app", **kw):
    return {"id": i, "title": f"task {i}", "status": status, "repo": repo, "cwd": "/x", "kind": "docs",
            "start_cell": "haiku", "final_cell": "haiku", "current_cell": None, "cost_usd": 0.03,
            "source": "queue", "created_at": NOW, "updated_at": NOW, **kw}


class CauceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.app = self.tmp / "code" / "app"
        self.app.mkdir(parents=True)
        self.other = self.tmp / "code" / "other"
        self.other.mkdir(parents=True)
        self.log = self.tmp / "calls.log"
        self.answers = self.tmp / "answers.json"
        fake = self.tmp / "bin" / "cauce"
        fake.parent.mkdir()
        fake.write_text(FAKE)
        fake.chmod(0o755)
        env = {"TANKA_CAUCE_BIN": str(fake), "FAKE_CAUCE_LOG": str(self.log), "FAKE_CAUCE_ANSWERS": str(self.answers),
               "TANKA_CAUCE_HOME": str(self.tmp / "cauce"), "TANKA_LINK_HOME": str(self.tmp / "link"), "TANKA_WORKSPACES": str(self.tmp / "workspaces")}
        for k, v in env.items():
            old = os.environ.get(k)
            os.environ[k] = v
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))
        old_home, old_ws = cl.HOME, kit.WORKSPACES
        cl.HOME, kit.WORKSPACES = self.tmp / "cauce", self.tmp / "workspaces"
        self.addCleanup(setattr, cl, "HOME", old_home)
        self.addCleanup(setattr, kit, "WORKSPACES", old_ws)
        cl._cache.clear()
        # tanka-link's side lives in a temporary home too: no test sees or writes the user's sessions.
        lk = cl._link()
        old_link = lk.HOME
        lk.HOME = self.tmp / "link"
        self.addCleanup(setattr, lk, "HOME", old_link)
        self.answer(board=self.board())

    def board(self, **kw):
        b = {"counts": {"needs_you": 1, "running": 1, "queued": 1, "done": 1},
             "needs_you": [task(1, "failed", asks="every cell on its ladder failed: read the attempts")],
             "running": [task(2, "running", current_cell="sonnet/medium", attempt=2, flow=FLOW,
                              worker={"seq": 2, "cell": "sonnet/medium", "max_turns": 30, "budget_usd": 1.9,
                                      "capabilities": ["livespec"], "started_at": NOW, "alive": True})],
             "queued": [{"repo": "github.com/o/app", "paused": False, "reason": None, "tasks": [task(3, "queued")]}],
             "done": [task(4, "done", branch="cauce/task-4", flow=dict(FLOW, steps=[
                 {"seq": 1, "cell": "haiku", "passed": 1}]))],
             "repos": {str(self.app): "github.com/o/app"}}
        b.update(kw)
        return b

    def answer(self, **kw):
        data = json.loads(self.answers.read_text()) if self.answers.exists() else {}
        data.update(kw)
        self.answers.write_text(json.dumps(data))
        cl._cache.clear()

    def calls(self):
        return [json.loads(x) for x in self.log.read_text().splitlines()] if self.log.exists() else []


class TestScope(CauceCase):
    def test_allow_deny_and_another_workspace_sees_nothing(self):
        self.assertEqual(cl.allow("duck", str(self.app)), "app")
        self.assertEqual(cl.repos("duck"), {"app": str(self.app)})
        self.assertEqual(cl.repos("goose"), {})
        self.assertEqual(cl.allow("duck", str(self.app)), "app")  # the same directory again is fine
        with self.assertRaisesRegex(cl.ToolError, "already has a repository named app"):
            cl.allow("duck", str(self.other), "app")
        with self.assertRaisesRegex(cl.ToolError, "not a directory"):
            cl.allow("duck", str(self.tmp / "nowhere"))
        with self.assertRaisesRegex(cl.ToolError, "cannot be a repository name"):
            cl.allow("duck", str(self.other), "a b")
        self.assertTrue(cl.deny("duck", "app"))
        self.assertFalse(cl.deny("duck", "app"))
        self.assertEqual(json.loads(cl.repos_file().read_text()), {})

    def test_an_unknown_repository_is_refused_before_cauce_runs(self):
        cl.allow("duck", str(self.app))
        with self.assertRaisesRegex(cl.ToolError, "may use: app"):
            cl.queue("duck", "other", "fix the login redirect for real")
        with self.assertRaisesRegex(cl.ToolError, "not a repository this workspace"):
            cl.unpause("goose", "app")
        self.assertEqual(self.calls(), [])

    def test_a_task_of_another_repository_is_refused(self):
        cl.allow("duck", str(self.app))
        self.answer(**{"show:9": {"task": task(9, "failed", repo="github.com/o/secret"), "attempts": []}})
        with self.assertRaisesRegex(cl.ToolError, "not in a repository this workspace may use"):
            cl.task("duck", 9)
        with self.assertRaisesRegex(cl.ToolError, "not in a repository this workspace may use"):
            cl.cancel("duck", 9)
        self.assertNotIn(["cancel", "9"], self.calls())


class TestCalls(CauceCase):
    def test_no_repository_means_no_call(self):
        self.assertEqual(cl.board("duck")["counts"]["needs_you"], 0)
        self.assertEqual(self.calls(), [])
        with self.assertRaisesRegex(cl.ToolError, "tanka cauce allow"):
            cl.board_text("duck")

    def test_the_board_asks_only_for_the_allowed_repositories_and_names_them(self):
        cl.allow("duck", str(self.app))
        b = cl.board("duck")
        self.assertEqual(self.calls(), [["board", "--full", "--repo", str(self.app)]])
        self.assertEqual(b["needs_you"][0]["repo_name"], "app")
        self.assertEqual(b["queued"][0]["repo_name"], "app")
        cl.board("duck")
        self.assertEqual(len(self.calls()), 1)  # cached between polls
        text = cl.board_text("duck")
        self.assertIn("#1 | app | failed | every cell on its ladder failed", text)
        self.assertIn("Agents working (1)", text)
        self.assertIn("#2 | app | sonnet/medium | attempt 2 | running under a minute | $0.03 so far | task 2", text)
        self.assertIn("#3 | app | queued | task 3", text)
        self.assertIn("#4 | app | passed at haiku | haiku ✓ | branch cauce/task-4 | task 4", text)
        with self.assertRaisesRegex(cl.ToolError, "not a repository"):
            cl.board_text("duck", "other")

    def test_prompts_answered_in_sessions_are_not_cards(self):
        """A newer cauce lists the sessions answering apart; an older one mixed every
        prompt into running and done, which must not bury the board in turns."""
        cl.allow("duck", str(self.app))
        prompts = [task(10 + i, "done", source="hook") for i in range(5)]
        self.answer(board=self.board(done=[task(4, "done")] + prompts,
                                     running=[task(2, "running", flow=FLOW), task(9, "running", source="hook")]))
        b = cl.board("duck")
        self.assertEqual([t["id"] for t in b["done"]], [4])
        self.assertEqual([t["id"] for t in b["running"]], [2])
        self.assertEqual([t["id"] for t in b["answering"]], [9])
        self.assertEqual((b["counts"]["done"], b["counts"]["running"], b["counts"]["answering"]), (1, 1, 1))
        self.answer(board=self.board(answering=[task(9, "running", source="hook")]))
        b = cl.board("duck", fresh=True)
        self.assertEqual(([t["id"] for t in b["running"]], [t["id"] for t in b["answering"]]), ([2], [9]))
        self.assertIn("answering", load_page().state("duck", self.tmp)["board"])

    def test_a_task_with_its_attempts(self):
        cl.allow("duck", str(self.app))
        self.answer(**{"show:1": {"task": task(1, "failed"), "branch": None, "attempts": [
            {"seq": 1, "cell": "haiku", "passed": 0, "failure": "code_bug", "summary": "missed the edge case",
             "move": "more_effort", "move_reason": "the work was shallow"},
            {"seq": 2, "cell": "sonnet/low", "passed": 0, "failure": "approach", "summary": "", "move": None}]}})
        text = cl.task_text("duck", 1)
        self.assertIn("#1 [failed] task 1", text)
        self.assertIn("attempt 1 at haiku: code_bug → more_effort: the work was shallow", text)
        self.assertIn("It needs the user", text)

    def test_queue_passes_the_task_and_its_check(self):
        cl.allow("duck", str(self.app))
        self.answer(queue={"id": 7, "title": "fix the login redirect", "status": "queued"})
        t = cl.queue("duck", "app", "fix the   login redirect", "pytest -q")
        self.assertEqual(t["id"], 7)
        self.assertEqual(self.calls()[-1], ["queue", "add", "fix the login redirect", "--repo", str(self.app),
                                            "--json", "--verify", "pytest -q"])
        with self.assertRaisesRegex(cl.ToolError, "too short"):
            cl.queue("duck", "app", "fix it")

    def test_cancel_only_what_can_be_cancelled(self):
        cl.allow("duck", str(self.app))
        self.answer(cancel="cancel requested for task #2", **{"show:2": {"task": task(2, "running")},
                                                              "show:4": {"task": task(4, "done")}})
        self.assertEqual(cl.cancel("duck", 2), {"cancelled": 2})
        self.assertIn(["cancel", "2"], self.calls())
        with self.assertRaisesRegex(cl.ToolError, "nothing to cancel"):
            cl.cancel("duck", 4)

    def test_cauce_missing_failing_or_not_json(self):
        os.environ["TANKA_CAUCE_BIN"] = str(self.tmp / "nope")
        with self.assertRaisesRegex(cl.ToolError, "not installed"):
            cl.call("board")
        self.assertIsNone(cl.version())
        os.environ.pop("TANKA_CAUCE_BIN")
        os.environ["PATH"], old = str(self.tmp / "empty"), os.environ["PATH"]
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.tmp / "claude")
        self.addCleanup(os.environ.pop, "CLAUDE_CONFIG_DIR", None)
        self.assertIsNone(cl.binary())
        plugin = self.tmp / "claude" / "plugins" / "cache" / "rixmerz" / "cauce" / "0.1.0" / "bin" / "cauce"
        plugin.parent.mkdir(parents=True)
        shutil.copy(self.tmp / "bin" / "cauce", plugin)
        self.assertEqual(cl.binary(), str(plugin))
        os.environ["PATH"] = old  # the fake runs on python3
        self.answer(board="not json")
        cl.allow("duck", str(self.app))
        with self.assertRaisesRegex(cl.ToolError, "not JSON"):
            cl.board("duck")
        with self.assertRaisesRegex(cl.ToolError, "no such thing"):
            cl.call("show", "5", "--json")


class TestTools(CauceCase):
    """The skill installed in a workspace and called the way the assistant calls it."""

    def setUp(self):
        super().setUp()
        self.ws = kit.WORKSPACES / "duck"
        (self.ws / ".tanka").mkdir(parents=True)
        (self.ws / ".tanka" / "policy.json").write_text("{}")
        tt.skills_dir(self.ws).mkdir(parents=True)

    def call(self, name, args):
        tools, problems = tt.scan(self.ws)
        self.assertEqual(problems, [])
        return tt.run_tool(self.ws, tools[name], args)

    def test_module_checks_and_installs_scoped(self):
        self.assertEqual(tm.check("cauce"), [])
        self.assertEqual(tm.install("cauce", self.ws, "duck"), 0)
        script = (tt.skills_dir(self.ws) / "cauce" / "tools" / "cauce_board.py").read_text()
        self.assertIn('SCOPE = "duck"', script)
        text, err = self.call("cauce_board", {})
        self.assertTrue(err)
        self.assertIn("tanka cauce allow", text)
        cl.allow("duck", str(self.app))
        text, err = self.call("cauce_board", {})
        self.assertFalse(err, text)
        self.assertIn("#1 | app | failed", text)
        self.answer(queue={"id": 8, "title": "add a test for the empty cart", "status": "queued"})
        text, err = self.call("cauce_queue", {"repo": "app", "task": "add a test for the empty cart"})
        self.assertFalse(err, text)
        self.assertIn("Queued #8 in app", text)
        text, err = self.call("cauce_queue", {"repo": "other", "task": "add a test for the empty cart"})
        self.assertTrue(err)
        self.assertIn("may use: app", text)
        self.answer(memory=[{"id": 2, "repo": "github.com/o/app", "title": "slow import", "state": "open", "fixes": []}])
        text, err = self.call("cauce_memory", {"query": "slow import"})
        self.assertFalse(err, text)
        self.assertIn("problem #2 [open] slow import (app)", text)


class TestProjectsSessionsProblems(CauceCase):
    def setUp(self):
        super().setUp()
        cl.allow("duck", str(self.app))
        self.answer(
            projects=[{"repo": "github.com/o/app", "dir": str(self.app), "exists": True, "sessions": 2, "tasks": {},
                       "last_seen": NOW},
                      {"repo": "github.com/o/other", "dir": str(self.other), "exists": True, "sessions": 1, "tasks": {},
                       "last_seen": NOW}],
            sessions=[{"id": "s-1", "repo": "github.com/o/app", "cwd": str(self.app), "prompts": 3,
                       "last_prompt": "fix the cart", "resume": f"cd {self.app} && claude --resume s-1"}],
            memory=[{"id": 1, "repo": "github.com/o/app", "title": "cart total off by one", "state": "solved",
                     "fixes": [{"description": "round first", "outcome": "failed", "why": "still off"},
                               {"description": "sum in cents", "outcome": "worked", "why": "",
                                "invalidated_on": NOW}]}])

    def test_projects_are_marked_and_only_known_ones_are_allowed_from_the_page(self):
        found = {p["dir"]: p for p in cl.projects("duck")}
        self.assertEqual(found[str(self.app)]["name"], "app")
        self.assertFalse(found[str(self.other)]["allowed"])
        with self.assertRaisesRegex(cl.ToolError, "has not worked in that directory"):
            cl.allow_known("duck", str(self.tmp))
        self.assertEqual(cl.allow_known("duck", str(self.other)), "other")
        self.assertEqual(sorted(cl.repos("duck")), ["app", "other"])

    def test_sessions_ask_for_the_allowed_repositories(self):
        found = cl.sessions("duck")
        self.assertEqual(found[0]["repo_name"], "app")
        self.assertIn(["sessions", "--json", "--repo", str(self.app)], self.calls())
        with self.assertRaisesRegex(cl.ToolError, "not a repository"):
            cl.sessions("duck", "other")
        self.assertEqual(cl.sessions("goose"), [])

    def test_problems_everywhere_for_the_page_and_scoped_for_tools(self):
        cl.problems("duck", "cart", everywhere=True)
        self.assertIn(["memory", "list", "--json", "--limit", "40", "--query", "cart"], self.calls())
        cl.problems("duck")
        self.assertIn(["memory", "list", "--json", "--limit", "40", "--repo", str(self.app)], self.calls())
        before = len(self.calls())
        self.assertEqual(cl.problems("goose"), [])
        self.assertEqual(len(self.calls()), before)
        text = cl.memory_text("duck", "cart")
        self.assertIn("problem #1 [solved] cart total off by one (app)", text)
        self.assertIn("failed: round first — still off", text)
        self.assertIn("disproved: sum in cents", text)
        with self.assertRaisesRegex(cl.ToolError, "tanka cauce allow"):
            cl.memory_text("goose")


OLD_CAUCE = {"stderr": "cauce: error: argument cmd: invalid choice: 'overview'"}
SID_APP, SID_OTHER, SID_QUIET = "a1b2c3d4-0000-4000-8000-000000000001", "a1b2c3d4-0000-4000-8000-000000000002", \
    "a1b2c3d4-0000-4000-8000-000000000003"


class TestSessionsAndSend(CauceCase):
    """A cauce session and a tanka-link session share Claude Code's id: the workspace sees each of its
    repositories' sessions with their work, and sends one a prompt only through tanka-link."""

    def setUp(self):
        super().setUp()
        cl.allow("duck", str(self.app))
        self.answer(overview={"since": NOW, "other": [], "sessions": [
            {"id": SID_APP, "name": "cart fixes", "cwd": str(self.app), "repo": "github.com/o/app", "last_seen_at": NOW,
             "last_prompt": "fix the cart", "answering": False, "resume": "claude --resume x", "counts": {},
             "tasks": [{"id": 1, "title": "task 1", "status": "failed", "repo": "github.com/o/app", "state": "needs_you",
                        "asks": "every cell failed"},
                       {"id": 2, "title": "task 2", "status": "running", "repo": "github.com/o/app", "state": "running",
                        "attempt": 2, "cell": "sonnet/medium", "since": NOW, "alive": True},
                       {"id": 7, "title": "elsewhere", "status": "queued", "repo": "github.com/o/other", "state": "queued"}]},
            {"id": SID_QUIET, "name": None, "cwd": str(self.app), "repo": "github.com/o/app", "last_seen_at": NOW,
             "last_prompt": "", "answering": False, "resume": "", "counts": {}, "tasks": []},
            {"id": SID_OTHER, "name": "secret", "cwd": str(self.other), "repo": "github.com/o/other", "last_seen_at": NOW,
             "last_prompt": "not yours", "answering": False, "resume": "", "counts": {}, "tasks": []}]},
            sessions=[{"id": SID_APP, "repo": "github.com/o/app", "cwd": str(self.app), "prompts": 3}])
        self.register(SID_APP, listening=True)
        self.register(SID_OTHER, listening=True)

    def register(self, sid, listening):
        home = self.tmp / "link" / "sessions"
        home.mkdir(parents=True, exist_ok=True)
        (home / f"{sid}.json").write_text(json.dumps({"id": sid, "cwd": "/x", "project": "x", "seen": time.time()}))
        if listening:
            (home / f"{sid}.pid").write_text(str(os.getpid()))

    def inbox(self, sid):
        box = self.tmp / "link" / "inbox" / sid
        return [json.loads(f.read_text()) for f in sorted(box.glob("*.json"))] if box.is_dir() else []

    def test_overview_keeps_this_workspace_s_sessions_and_tasks_and_joins_the_link(self):
        found = cl.overview("duck")
        self.assertEqual([s["id"] for s in found], [SID_APP, SID_QUIET])  # not the other repository's
        self.assertEqual(found[0]["repo_name"], "app")
        self.assertEqual([t["id"] for t in found[0]["tasks"]], [1, 2])  # its task in another repository is not shown
        self.assertEqual((found[0]["link"], found[1]["link"]), ("listening", None))
        self.assertIn(["overview", "--json", "--hours", "24", "--limit", "50"], self.calls())
        self.assertEqual(cl.overview("goose"), [])

    def test_sessions_carry_their_work_and_survive_an_older_cauce(self):
        found = cl.sessions("duck")
        self.assertEqual(([t["id"] for t in found[0]["tasks"]], found[0]["link"]), ([1, 2], "listening"))
        self.answer(overview=OLD_CAUCE)
        found = cl.sessions("duck")
        self.assertEqual((found[0]["tasks"], found[0]["link"]), ([], "listening"))

    def test_sessions_text_numbers_them_with_their_work(self):
        text = cl.sessions_text("duck")
        self.assertIn("1 | app | cart fixes | can be sent a prompt now", text)
        self.assertIn("#1 [failed] task 1 — waits on the user: every cell failed", text)
        self.assertIn("#2 [running] task 2 — attempt 2 on sonnet/medium", text)
        self.assertIn("2 | app | a1b2c3d4 | cannot be sent a prompt", text)
        self.assertNotIn("secret", text)
        with self.assertRaisesRegex(cl.ToolError, "not a repository"):
            cl.sessions_text("duck", "other")

    def test_send_goes_through_tanka_link_only_to_this_workspace_s_listening_sessions(self):
        cl.sessions_text("duck")
        out = cl.send_text("duck", 1, "Run the cart tests and fix what fails", "Ana")
        self.assertIn("Sent to the session in app: it reads it now", out)
        [msg] = self.inbox(SID_APP)
        self.assertEqual((msg["from"], msg["text"]), ("Ana", "Run the cart tests and fix what fails"))
        with self.assertRaisesRegex(cl.ToolError, "cannot receive a prompt"):
            cl.send_text("duck", 2, "Run the cart tests and fix what fails", "Ana")
        with self.assertRaisesRegex(cl.ToolError, "no session number 3"):
            cl.send_text("duck", 3, "Run the cart tests and fix what fails", "Ana")
        with self.assertRaisesRegex(cl.ToolError, "not a cauce session"):
            cl.send("duck", SID_OTHER, "Run the cart tests and fix what fails", "Ana")
        self.assertEqual(self.inbox(SID_OTHER), [])

    def test_the_tools_as_the_assistant_calls_them_and_the_page_s_send(self):
        ws = kit.WORKSPACES / "duck"
        (ws / ".tanka").mkdir(parents=True)
        (ws / ".tanka" / "policy.json").write_text("{}")
        (ws / ".tanka" / "persona.json").write_text(json.dumps({"name": "Ana"}))
        tt.skills_dir(ws).mkdir(parents=True)
        self.assertEqual(tm.install("cauce", ws, "duck"), 0)
        tools, problems = tt.scan(ws)
        self.assertEqual(problems, [])
        self.assertEqual(tools["cauce_send"]["effect"], "send")
        text, err = tt.run_tool(ws, tools["cauce_sessions"], {})
        self.assertFalse(err, text)
        self.assertIn("1 | app | cart fixes", text)
        text, err = tt.run_tool(ws, tools["cauce_send"], {"number": 1, "text": "Run the cart tests and fix what fails"})
        self.assertFalse(err, text)
        self.assertEqual([m["from"] for m in self.inbox(SID_APP)], ["Ana"])
        page = load_page()
        self.assertTrue(page.ACTIONS["send"]("duck", ws, {"id": SID_APP, "text": "and then the docs"})["ok"])
        self.assertEqual([m["from"] for m in self.inbox(SID_APP)], ["Ana", "user"])
        with self.assertRaisesRegex(cl.ToolError, "not a cauce session"):
            page.ACTIONS["send"]("duck", ws, {"id": SID_OTHER, "text": "and then the docs"})
        self.assertIn("cauce_send", page.hint("duck", ws))

    def test_an_older_cauce_is_named(self):
        self.answer(overview=OLD_CAUCE)
        with self.assertRaisesRegex(cl.ToolError, "needs cauce 0.6.15 or newer"):
            cl.sessions_text("duck")


def load_page():
    spec = importlib.util.spec_from_file_location("page_cauce_test", REPO / "modules" / "cauce" / "page.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestPage(CauceCase):
    def setUp(self):
        super().setUp()
        self.page = load_page()
        self.ws = kit.WORKSPACES / "duck"
        (self.ws / ".claude" / "skills" / "cauce").mkdir(parents=True)

    def test_installed_state_and_chat(self):
        self.assertTrue(self.page.installed(self.ws))
        self.assertFalse(self.page.installed(self.tmp))
        self.assertEqual(self.page.state("duck", self.ws)["board"], None)
        cl.allow("duck", str(self.app))
        state = self.page.state("duck", self.ws)
        self.assertEqual(state["repos"], ["app"])
        self.assertEqual(state["board"]["counts"]["needs_you"], 1)
        self.answer(board=self.board(done=[task(4, "done", branch="cauce/task-4"),
                                           task(5, "done", source="hook")]))
        items = self.page.stream("duck", self.ws)
        self.assertEqual([(i["id"], i["status"]) for i in items], [(1, "failed"), (4, "done")])  # not the session's #5
        self.assertEqual(items[1]["branch"], "cauce/task-4")
        lines = self.page.context("duck", self.ws, time.time() - 3600, time.time() + 60)
        self.assertIn("cauce task #1 in app ended failed", lines[0][1])
        self.assertIn("cauce_queue", self.page.hint("duck", self.ws))
        self.assertTrue(self.page.health()[0]["ok"])

    def test_an_unreachable_cauce_is_said_not_raised(self):
        cl.allow("duck", str(self.app))
        self.answer(board="not json")
        self.assertIn("not JSON", self.page.state("duck", self.ws)["error"])
        self.assertEqual(self.page.stream("duck", self.ws), [])

    def test_actions_and_details(self):
        cl.allow("duck", str(self.app))
        self.answer(lanes="unpaused", work="ran", **{"show:1": {"task": task(1, "failed"), "attempts": [
            {"seq": 1, "cell": "haiku", "passed": 0, "failure": "code_bug", "changed_paths": ["a.py"]}]}})
        d = self.page.GETS["task"]("duck", self.ws, {"id": "1"})
        self.assertEqual(d["attempts"][0]["cell"], "haiku")
        self.assertNotIn("changed_paths", d["attempts"][0])
        with self.assertRaisesRegex(cl.ToolError, "a number"):
            self.page.GETS["task"]("duck", self.ws, {"id": "x"})
        self.assertEqual(self.page.ACTIONS["unpause"]("duck", self.ws, {"repo": "app"}), {"unpaused": "app"})
        self.assertIn(["lanes", "--unpause", str(self.app)], self.calls())
        started = self.page.ACTIONS["work"]("duck", self.ws, {"repo": "app"})
        self.assertEqual(started["started"], "app")
        for _ in range(50):
            if ["work", "--repo", str(self.app)] in self.calls():
                break
            time.sleep(0.05)
        self.assertIn(["work", "--repo", str(self.app)], self.calls())

    def test_the_page_lists_and_queues_by_the_user_s_hand(self):
        cl.allow("duck", str(self.app))
        self.answer(projects=[], sessions=[], memory=[], queue={"id": 9, "title": "write the docs", "status": "queued"})
        self.assertEqual(self.page.GETS["projects"]("duck", self.ws, {}), {"projects": []})
        self.assertEqual(self.page.GETS["sessions"]("duck", self.ws, {"repo": "app"}), {"sessions": []})
        self.page.GETS["problems"]("duck", self.ws, {"q": "x", "all": "1"})
        self.assertIn(["memory", "list", "--json", "--limit", "40", "--query", "x"], self.calls())
        t = self.page.ACTIONS["queue"]("duck", self.ws, {"repo": "app", "text": "write the docs for the cart", "check": ""})
        self.assertEqual(t["id"], 9)
        self.assertIn(["queue", "add", "write the docs for the cart", "--repo", str(self.app), "--json"], self.calls())
        with self.assertRaisesRegex(cl.ToolError, "has not worked"):
            self.page.ACTIONS["allow"]("duck", self.ws, {"dir": str(self.other)})


if __name__ == "__main__":
    unittest.main()
