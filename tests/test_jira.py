"""Jira module: project scoping, JQL wrapping, ADF, retries and the token rule, with a fake HTTP opener."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "modules" / "jira"))
import jira  # noqa: E402

FAKE_TOKEN = "tok-" + "z" * 20  # built at runtime: a fake value, never a real token
TOOLS = REPO / "modules" / "jira" / "skill" / "tools"


class Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, body="", headers=None):
    return urllib.error.HTTPError("https://your-company.atlassian.net/x", code, "err", headers or {}, io.BytesIO(body.encode()))


class JiraCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "scopes.json").write_text(json.dumps({"scopes": {
            "work": {"projects": ["PROJ", "ops"], "write": True},
            "reader": {"projects": ["PROJ"], "write": False}}}))
        self.patch("HOME", self.tmp)
        self.patch("SCOPES", self.tmp / "scopes.json")
        self.calls, self.replies = [], []
        self.patch("OPENER", self.opener)
        self.sleeps = []
        self.patch("SLEEP", self.sleeps.append)
        env = {"TANKA_JIRA_SITE": "https://your-company.atlassian.net", "TANKA_JIRA_EMAIL": "someone@example.com",
               "TANKA_JIRA_TOKEN_VAR": "JIRA_TEST_TOKEN", "JIRA_TEST_TOKEN": FAKE_TOKEN}
        for k, v in env.items():
            old = os.environ.get(k)
            os.environ[k] = v
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))

    def patch(self, name, value):
        old = getattr(jira, name)
        setattr(jira, name, value)
        self.addCleanup(setattr, jira, name, old)

    def opener(self, req, timeout):
        body = json.loads(req.data) if req.data else None
        self.calls.append((req.get_method(), req.full_url, body))
        if not self.replies:
            raise AssertionError("unexpected network call")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return Resp(json.dumps(r).encode() if r is not None else b"")


class TestScope(JiraCase):
    def test_project_outside_scope_is_refused_before_the_network(self):
        with self.assertRaisesRegex(jira.ToolError, "not a Jira project this assistant may use"):
            jira.issue("work", "OTHER-1")
        with self.assertRaisesRegex(jira.ToolError, "not a Jira project"):
            jira.create("work", "OTHER", "Task", "x")
        with self.assertRaisesRegex(jira.ToolError, "not an issue key"):
            jira.issue("work", "PROJ-1/../../x")
        with self.assertRaisesRegex(jira.ToolError, "No Jira project is assigned"):
            jira.issue("nobody", "PROJ-1")
        self.assertEqual(self.calls, [])

    def test_keys_are_case_insensitive(self):
        self.assertEqual(jira.check_key("work", "ops-7"), "OPS-7")

    def test_read_only_scope_cannot_write(self):
        for call in (lambda: jira.create("reader", "PROJ", "Task", "x"),
                     lambda: jira.transition("reader", "PROJ-1", "Done"),
                     lambda: jira.comment("reader", "PROJ-1", "hi"),
                     lambda: jira.link("reader", "PROJ-1", "PROJ-2")):
            with self.assertRaisesRegex(jira.ToolError, "only read"):
                call()
        self.assertEqual(self.calls, [])

    def test_link_needs_both_ends_in_scope(self):
        with self.assertRaisesRegex(jira.ToolError, "not a Jira project"):
            jira.link("work", "PROJ-1", "OTHER-2")
        with self.assertRaisesRegex(jira.ToolError, "not a Jira project"):
            jira.link("work", "OTHER-1", "PROJ-2")
        self.assertEqual(self.calls, [])

    def test_parent_must_be_in_scope(self):
        with self.assertRaisesRegex(jira.ToolError, "not a Jira project"):
            jira.create("work", "PROJ", "Task", "x", parent="OTHER-1")

    def test_missing_scopes_file(self):
        (self.tmp / "scopes.json").unlink()
        with self.assertRaisesRegex(jira.ToolError, "does not exist"):
            jira.search("work", "status = Done")


class TestJql(JiraCase):
    def test_wrap(self):
        self.assertEqual(jira.wrap_jql("work", "status = Done"), "project in (OPS, PROJ) AND (status = Done)")
        self.assertEqual(jira.wrap_jql("work", ""), "project in (OPS, PROJ)")

    def test_order_by_is_moved_after_the_wrap(self):
        self.assertEqual(jira.wrap_jql("work", 'text ~ "a (b" ORDER BY updated DESC'),
                         'project in (OPS, PROJ) AND (text ~ "a (b") ORDER BY updated DESC')
        self.assertEqual(jira.wrap_jql("work", "order by created"), "project in (OPS, PROJ) order by created")

    def test_escapes_are_refused(self):
        for bad in ("status = Done) OR (project = OTHER", "status = Done ORDER BY rank) OR (project = OTHER",
                    "ORDER BY created OR project = OTHER", "a = 1 ORDER BY x ORDER BY y", "summary ~ \"open", "(a = 1"):
            with self.subTest(jql=bad), self.assertRaises(jira.ToolError):
                jira.wrap_jql("work", bad)

    def test_search_sends_the_wrapped_query_and_pages(self):
        issue = lambda k: {"key": k, "fields": {"summary": "Fix  login", "status": {"name": "To Do"},
                                                "issuetype": {"name": "Bug"}, "assignee": None}}
        self.replies = [{"issues": [issue("PROJ-1")], "nextPageToken": "n1"}, {"issues": [issue("PROJ-2")]}]
        out = jira.search("work", "status = Done", 2)
        self.assertEqual(self.calls[0][2]["jql"], "project in (OPS, PROJ) AND (status = Done)")
        self.assertEqual(self.calls[1][2]["nextPageToken"], "n1")
        self.assertIn("/rest/api/3/search/jql", self.calls[0][1])
        self.assertIn("PROJ-2 | To Do | Bug | unassigned | Fix login", out)
        self.assertTrue(out.startswith("2 issue(s)"))


class TestAdf(unittest.TestCase):
    def test_round_trip(self):
        text = "First line\nsecond line\n\nNew paragraph"
        doc = jira.to_adf(text)
        self.assertEqual(len(doc["content"]), 2)
        self.assertEqual(doc["content"][0]["content"][1], {"type": "hardBreak"})
        self.assertEqual(jira.from_adf(doc), text)

    def test_lists_and_mentions(self):
        doc = {"type": "doc", "content": [{"type": "bulletList", "content": [
            {"type": "listItem", "content": [{"type": "paragraph", "content": [
                {"type": "mention", "attrs": {"text": "@Clara"}}, {"type": "text", "text": " to check"}]}]}]}]}
        self.assertEqual(jira.from_adf(doc), "- @Clara to check")
        self.assertEqual(jira.from_adf(None), "")


class TestHttp(JiraCase):
    def test_retries_honour_retry_after(self):
        self.replies = [http_error(429, headers={"Retry-After": "3"}), http_error(503), {"key": "PROJ-9"}]
        self.assertIn("PROJ-9", jira.create("work", "PROJ", "Task", "x"))
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.sleeps[0], 3.0)

    def test_gives_up_after_four_tries(self):
        self.replies = [http_error(502) for _ in range(4)]
        with self.assertRaisesRegex(jira.ToolError, "HTTP 502"):
            jira.issue("work", "PROJ-1")
        self.assertEqual(len(self.calls), 4)

    def test_token_never_in_errors(self):
        body = json.dumps({"errorMessages": [f"bad credentials {FAKE_TOKEN}"]}) + "x" * 1000
        self.replies = [http_error(401, body)]
        with self.assertRaises(jira.ToolError) as cm:
            jira.issue("work", "PROJ-1")
        msg = str(cm.exception)
        self.assertNotIn(FAKE_TOKEN, msg)
        self.assertIn("HTTP 401", msg)
        self.assertLess(len(msg), 420)
        self.replies = [urllib.error.URLError(f"proxy said {FAKE_TOKEN}")]
        with self.assertRaises(jira.ToolError) as cm:
            jira.issue("work", "PROJ-1")
        self.assertNotIn(FAKE_TOKEN, str(cm.exception))

    def test_basic_auth_is_sent_but_not_shown(self):
        seen = []
        self.patch("OPENER", lambda req, t: seen.append(req.get_header("Authorization")) or Resp(b"{}"))
        jira.request("GET", "/rest/api/3/myself")
        self.assertTrue(seen[0].startswith("Basic "))

    def test_missing_token(self):
        os.environ.pop("JIRA_TEST_TOKEN")
        with self.assertRaisesRegex(jira.ToolError, "JIRA_TEST_TOKEN"):
            jira.issue("work", "PROJ-1")
        self.assertEqual(self.calls, [])


class TestOperations(JiraCase):
    def test_transition_by_target_status(self):
        self.replies = [{"transitions": [{"id": "11", "name": "Start", "to": {"name": "In Progress"}},
                                         {"id": "31", "name": "Finish", "to": {"name": "Done"}}]}, None]
        self.assertEqual(jira.transition("work", "PROJ-1", "done"), "PROJ-1 moved to Done.")
        self.assertEqual(self.calls[1][2], {"transition": {"id": "31"}})

    def test_transition_unavailable_lists_options(self):
        self.replies = [{"transitions": [{"id": "11", "name": "Start", "to": {"name": "In Progress"}}]}]
        with self.assertRaisesRegex(jira.ToolError, "Available: In Progress"):
            jira.transition("work", "PROJ-1", "Done")

    def test_link_direction(self):
        self.replies = [None]
        self.assertEqual(jira.link("work", "PROJ-1", "OPS-2"), "Linked: PROJ-1 blocks OPS-2.")
        self.assertEqual(self.calls[0][2]["inwardIssue"], {"key": "PROJ-1"})

    def test_create_fields(self):
        self.replies = [{"key": "PROJ-5"}]
        jira.create("work", "proj", "Task", "Title", "Body", "a, b c", "PROJ-1")
        f = self.calls[0][2]["fields"]
        self.assertEqual(f["labels"], ["a", "b-c"])
        self.assertEqual(f["parent"], {"key": "PROJ-1"})
        self.assertEqual(f["description"]["type"], "doc")

    def test_issue_output_is_capped(self):
        comments = [{"author": {"displayName": "Clara Client"}, "created": "2026-01-02T00:00:00",
                     "body": jira.to_adf("y" * 3000)} for _ in range(15)]
        self.replies = [{"key": "PROJ-1", "fields": {"summary": "S", "description": jira.to_adf("x" * 9000),
                                                     "comment": {"comments": comments}}}]
        out = jira.cap(jira.issue("work", "PROJ-1"))
        self.assertLessEqual(len(out), 6000)
        self.assertIn("output truncated", out)


class TestTools(unittest.TestCase):
    def test_every_script_is_scoped(self):
        for py in TOOLS.glob("*.py"):
            self.assertIn('SCOPE = "__SCOPE__"', py.read_text())
        self.assertEqual(len(list(TOOLS.glob("*.json"))), 6)

    def test_install_and_checks(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        env = dict(os.environ, TANKA_WORKSPACES=str(tmp / "workspaces"), TANKA_JIRA_HOME=str(tmp / "home"))
        tanka = lambda *a: subprocess.run([str(REPO / "bin" / "tanka"), *a], capture_output=True, text=True, env=env)
        tanka("init", "tickets")
        p = tanka("install", "jira", "tickets")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('"PROJ"', (tmp / "home" / "scopes.json").read_text())
        self.assertIn('SCOPE = "tickets"', (tmp / "workspaces" / "tickets" / ".claude/skills/jira/tools/jira_link.py").read_text())
        self.assertIn("6/15 tools loaded, 0 problem(s), 0 warning(s)", tanka("tools", "check", "tickets").stdout)
        self.assertIn("ok", tanka("modules", "check", "jira").stdout)
        st = tanka("jira", "status")
        self.assertEqual(st.returncode, 0, st.stderr)
        self.assertIn("PROJ", st.stdout)
        self.assertNotIn(FAKE_TOKEN, st.stdout)


if __name__ == "__main__":
    unittest.main()
