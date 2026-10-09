"""Tanka hook tests, driving each hook with the JSON event Claude Code sends."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import tanka_common as tc  # noqa: E402
TEMPLATE = REPO / "workspace-template"


def run_hook(script: str, payload: dict, ws: Path) -> tuple[int, dict, str]:
    env = dict(os.environ, CLAUDE_PROJECT_DIR=str(ws), CLAUDE_PLUGIN_ROOT=str(REPO / "plugin"))
    p = subprocess.run([sys.executable, str(SCRIPTS / script)], input=json.dumps(payload), capture_output=True, text=True, env=env, cwd=str(ws))
    out = {}
    if p.stdout.strip():
        try:
            out = json.loads(p.stdout)
        except json.JSONDecodeError:
            out = {"_raw": p.stdout}
    return p.returncode, out, p.stderr


def decision(out: dict) -> str:
    return out.get("hookSpecificOutput", {}).get("permissionDecision", "")


def reason(out: dict) -> str:
    return out.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")


class HookTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="tanka-test-"))
        shutil.copytree(TEMPLATE, self.tmp, dirs_exist_ok=True)
        # These suites exercise name-based classification of foreign MCP
        # servers, which only applies when the policy lets them load.
        pol = self.tmp / ".tanka" / "policy.json"
        data = json.loads(pol.read_text(encoding="utf-8"))
        data["external_mcp"] = "policy"
        pol.write_text(json.dumps(data), encoding="utf-8")
        self.sid = "sess-test"
        self.pid = "prompt-1"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def pre(self, tool, tool_input, tool_use_id="tu1", pid=None):
        return run_hook("hook_pre_tool.py", {
            "session_id": self.sid, "prompt_id": pid or self.pid, "cwd": str(self.tmp),
            "hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input, "tool_use_id": tool_use_id,
        }, self.tmp)

    def post(self, tool, tool_input, tool_use_id="tu1", ok=True, pid=None):
        script = "hook_post_tool.py" if ok else "hook_post_tool_failure.py"
        payload = {"session_id": self.sid, "prompt_id": pid or self.pid, "cwd": str(self.tmp), "tool_name": tool,
                   "tool_input": tool_input, "tool_use_id": tool_use_id}
        if ok:
            payload.update(hook_event_name="PostToolUse", tool_response="ok")
        else:
            payload.update(hook_event_name="PostToolUseFailure", error="boom")
        return run_hook(script, payload, self.tmp)

    def stop(self, msg, active=False, pid=None):
        return run_hook("hook_stop.py", {"session_id": self.sid, "prompt_id": pid or self.pid, "cwd": str(self.tmp),
                                         "hook_event_name": "Stop", "stop_hook_active": active, "last_assistant_message": msg}, self.tmp)

    def set_objective(self, **over):
        obj = {"title": "T", "goal": "G", "done_when": ["x"], "allowed_tool_classes": ["read", "draft"], "status": "active"}
        obj.update(over)
        p = self.tmp / ".tanka" / "state" / "objective.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj), encoding="utf-8")


class TestPolicy(HookTestCase):
    def test_read_allowed(self):
        code, out, _ = self.pre("mcp__gmail__get_message", {"id": "1"})
        self.assertEqual(code, 0)
        self.assertEqual(decision(out), "allow")

    def test_destructive_denied(self):
        for tool in ("mcp__gmail__trash_message", "mcp__drive__delete_file", "mcp__gmail__mark_message_spam"):
            code, out, _ = self.pre(tool, {"id": "1"}, tool_use_id=tool)
            self.assertEqual(decision(out), "deny", tool)
            self.assertIn("destructive", reason(out))

    def test_modify_asks(self):
        code, out, _ = self.pre("mcp__gmail__label_message", {"id": "1", "label": "x"})
        self.assertEqual(decision(out), "ask")

    def test_unknown_asks(self):
        code, out, _ = self.pre("mcp__foo__frobnicate", {"a": 1})
        self.assertEqual(decision(out), "ask")

    def test_send_valid_asks_with_summary(self):
        code, out, _ = self.pre("mcp__gmail__send_message", {"to": "ana@acme.com", "subject": "Meeting", "body": "Hi Ana, confirming Thursday at 10. Best."})
        self.assertEqual(decision(out), "ask")
        self.assertIn("ana@acme.com", reason(out))
        self.assertIn("Meeting", reason(out))

    def test_send_with_placeholder_denied(self):
        code, out, _ = self.pre("mcp__gmail__send_message", {"to": "ana@acme.com", "subject": "Hello", "body": "Dear [NAME], I am writing to confirm."})
        self.assertEqual(decision(out), "deny")
        self.assertIn("placeholder", reason(out))

    def test_send_short_body_denied(self):
        code, out, _ = self.pre("mcp__gmail__reply", {"to": "ana@acme.com", "body": "ok"})
        self.assertEqual(decision(out), "deny")

    def test_send_blocked_recipient(self):
        code, out, _ = self.pre("mcp__gmail__send_message", {"to": "noreply@x.com", "body": "Hello, this is a long enough test message."})
        self.assertEqual(decision(out), "deny")
        self.assertIn("blocked by policy", reason(out))

    def test_send_secret_denied(self):
        code, out, _ = self.pre("mcp__slack__post_message", {"channel": "#gen", "text": "token: sk-ant-abcdefghijklmnop123456"})
        self.assertEqual(decision(out), "deny")
        self.assertIn("secret", reason(out))

    def test_send_nested_recipients(self):
        code, out, _ = self.pre("mcp__mail__send", {"message": {"to": ["a@b.com", "c@d.com"], "body": "Hi all, confirming tomorrow's agenda."}})
        self.assertEqual(decision(out), "ask")
        self.assertIn("a@b.com", reason(out))

    def test_allowlist_from_policy(self):
        pol = json.loads((self.tmp / ".tanka" / "policy.json").read_text())
        pol["send_validation"]["recipient_allowlist"] = [r"@acme\.com$"]
        (self.tmp / ".tanka" / "policy.json").write_text(json.dumps(pol))
        code, out, _ = self.pre("mcp__gmail__send_message", {"to": "x@otro.com", "body": "Hello, a message long enough to pass."})
        self.assertEqual(decision(out), "deny")
        self.assertIn("allowlist", reason(out))


class TestBuiltin(HookTestCase):
    def test_bash_denied(self):
        code, out, _ = self.pre("Bash", {"command": "ls"})
        self.assertEqual(decision(out), "deny")

    def test_write_outside_denied(self):
        code, out, _ = self.pre("Write", {"file_path": str(self.tmp / "hack.py"), "content": "x"})
        self.assertEqual(decision(out), "deny")

    def test_write_traversal_denied(self):
        code, out, _ = self.pre("Write", {"file_path": ".tanka/state/../../../etc/x", "content": "x"})
        self.assertEqual(decision(out), "deny")

    def test_write_draft_allowed(self):
        code, out, _ = self.pre("Write", {"file_path": ".tanka/drafts/2026-09-16-ana.md", "content": "x"})
        self.assertEqual(code, 0)
        self.assertEqual(decision(out), "allow")

    def test_write_persona_allowed_headless(self):
        """Saving the profile must not need a permission prompt, or the
        first-run language question can never be answered in a delegated run."""
        code, out, _ = self.pre("Write", {"file_path": ".tanka/persona.json", "content": "{}"})
        self.assertEqual(decision(out), "allow")

    def test_objective_schema_enforced(self):
        bad = {"title": "t", "goal": "g", "done_when": [], "allowed_tool_classes": ["destructive"], "status": "wat"}
        code, out, _ = self.pre("Write", {"file_path": ".tanka/state/objective.json", "content": json.dumps(bad)})
        self.assertEqual(decision(out), "deny")
        r = reason(out)
        self.assertIn("done_when", r)
        self.assertIn("destructive", r)
        self.assertIn("status", r)
        good = {"title": "t", "goal": "g", "done_when": ["a"], "allowed_tool_classes": ["read"], "status": "active"}
        code, out, _ = self.pre("Write", {"file_path": ".tanka/state/objective.json", "content": json.dumps(good)}, tool_use_id="tu2")
        self.assertEqual(decision(out), "allow")

    def test_read_allowed(self):
        code, out, _ = self.pre("Read", {"file_path": "x"})
        self.assertEqual(decision(out), "")


class TestObjective(HookTestCase):
    def test_class_outside_objective_denied(self):
        self.set_objective(allowed_tool_classes=["read"])
        code, out, _ = self.pre("mcp__gmail__send_message", {"to": "a@b.com", "body": "Hello, this is a valid and long enough body."})
        self.assertEqual(decision(out), "deny")
        self.assertIn("does not authorise the 'send' class", reason(out))

    def test_a_routines_objective_does_not_hold_the_users_chat(self):
        call = ("mcp__gmail__label_message", {"id": "1"})
        self.set_objective(id="auto-watch", allowed_tool_classes=["read", "draft"])
        self.assertEqual(decision(self.pre(*call)[1]), "deny")
        with mock.patch.dict(os.environ, {"TANKA_CHAT": "1"}):
            self.assertNotEqual(decision(self.pre(*call, tool_use_id="tu2")[1]), "deny")
            self.set_objective(id="mine", allowed_tool_classes=["read"])
            self.assertEqual(decision(self.pre(*call, tool_use_id="tu3")[1]), "deny")  # the user's own still holds

    def test_read_always_allowed_under_objective(self):
        self.set_objective(allowed_tool_classes=["draft"])
        code, out, _ = self.pre("mcp__gmail__list_messages", {})
        self.assertEqual(decision(out), "allow")

    def test_may_send_false(self):
        self.set_objective(allowed_tool_classes=["read", "send"], may_send=False)
        code, out, _ = self.pre("mcp__gmail__send_message", {"to": "a@b.com", "body": "Hello, this is a valid and long enough body."})
        self.assertEqual(decision(out), "deny")
        self.assertIn("may_send", reason(out))

    def test_allowed_tools_whitelist(self):
        self.set_objective(allowed_tool_classes=["read", "modify"], allowed_tools=["mcp__gmail__label_message"])
        code, out, _ = self.pre("mcp__gmail__untrash_message", {"id": "1"})
        self.assertEqual(decision(out), "deny")
        code, out, _ = self.pre("mcp__gmail__label_message", {"id": "1"}, tool_use_id="tu2")
        self.assertEqual(decision(out), "ask")

    def test_objective_budget(self):
        self.set_objective(allowed_tool_classes=["read"], max_tool_calls=2)
        for i in range(2):
            code, out, _ = self.pre("mcp__gmail__get_message", {"id": str(i)}, tool_use_id=f"t{i}")
            self.assertEqual(decision(out), "allow")
        code, out, _ = self.pre("mcp__gmail__get_message", {"id": "9"}, tool_use_id="t9")
        self.assertEqual(decision(out), "deny")
        self.assertIn("budget", reason(out))

    def test_done_objective_not_enforced(self):
        self.set_objective(allowed_tool_classes=["read"], status="done")
        code, out, _ = self.pre("mcp__gmail__label_message", {"id": "1"})
        self.assertEqual(decision(out), "ask")


class TestLoopGuard(HookTestCase):
    def test_identical_call_blocked_third_time(self):
        ti = {"id": "42"}
        for i in range(2):
            code, out, _ = self.pre("mcp__gmail__get_message", ti, tool_use_id=f"t{i}")
            self.assertEqual(decision(out), "allow")
            self.post("mcp__gmail__get_message", ti, tool_use_id=f"t{i}")
        code, out, _ = self.pre("mcp__gmail__get_message", ti, tool_use_id="t3")
        self.assertEqual(decision(out), "deny")
        self.assertIn("LOOP", reason(out))

    def test_counters_reset_on_new_prompt(self):
        ti = {"id": "42"}
        for i in range(2):
            self.pre("mcp__gmail__get_message", ti, tool_use_id=f"t{i}")
        code, out, _ = self.pre("mcp__gmail__get_message", ti, tool_use_id="t3", pid="prompt-2")
        self.assertEqual(decision(out), "allow")

    def test_consecutive_failures(self):
        for i in range(3):
            self.pre("mcp__cal__list_events", {"day": str(i)}, tool_use_id=f"t{i}")
            code, out, err = self.post("mcp__cal__list_events", {"day": str(i)}, tool_use_id=f"t{i}", ok=False)
            self.assertEqual(code, 0)
        self.assertIn("DO NOT retry", out["hookSpecificOutput"]["additionalContext"])
        code, out, _ = self.pre("mcp__cal__list_events", {"day": "9"}, tool_use_id="t9")
        self.assertEqual(decision(out), "deny")
        self.assertIn("failed", reason(out))

    def test_a_new_message_lets_a_failed_tool_be_tried_again(self):
        """The user asking again (often after fixing what failed) is not the model looping on its own."""
        for i in range(3):
            self.pre("mcp__cal__list_events", {"day": str(i)}, tool_use_id=f"t{i}")
            self.post("mcp__cal__list_events", {"day": str(i)}, tool_use_id=f"t{i}", ok=False)
        code, out, _ = self.pre("mcp__cal__list_events", {"day": "9"}, tool_use_id="t9")
        self.assertEqual(decision(out), "deny")
        code, out, _ = self.pre("mcp__cal__list_events", {"day": "9"}, tool_use_id="t10", pid="prompt-2")
        self.assertEqual(decision(out), "allow")

    def test_failure_counter_resets_on_success(self):
        for i in range(2):
            self.pre("mcp__cal__list_events", {"day": str(i)}, tool_use_id=f"t{i}")
            self.post("mcp__cal__list_events", {"day": str(i)}, tool_use_id=f"t{i}", ok=False)
        self.pre("mcp__cal__list_events", {"day": "5"}, tool_use_id="t5")
        self.post("mcp__cal__list_events", {"day": "5"}, tool_use_id="t5", ok=True)
        code, out, _ = self.pre("mcp__cal__list_events", {"day": "6"}, tool_use_id="t6")
        self.assertEqual(decision(out), "allow")

    def test_turn_budget(self):
        pol = json.loads((self.tmp / ".tanka" / "policy.json").read_text())
        pol.setdefault("loop_guard", {})["max_calls_per_turn"] = 3  # the template leaves the guard to the profile
        (self.tmp / ".tanka" / "policy.json").write_text(json.dumps(pol))
        for i in range(3):
            code, out, _ = self.pre("mcp__gmail__get_message", {"id": str(i)}, tool_use_id=f"t{i}")
            self.assertEqual(decision(out), "allow")
        code, out, _ = self.pre("mcp__gmail__get_message", {"id": "x"}, tool_use_id="tx")
        self.assertEqual(decision(out), "deny")
        self.assertIn("budget", reason(out))


class TestStop(HookTestCase):
    def test_claim_without_evidence_blocked(self):
        code, out, err = self.stop("Done, I have sent the email to Ana.")
        self.assertEqual(code, 2)
        self.assertIn("send", err)

    def test_claim_with_evidence_ok(self):
        ti = {"to": "ana@acme.com", "body": "Hi Ana, confirming Thursday at 10."}
        self.pre("mcp__gmail__send_message", ti)
        self.post("mcp__gmail__send_message", ti)
        code, out, err = self.stop("I have sent the email to Ana (id 123).\n\nStatus: done")
        self.assertEqual(code, 0)

    def test_negated_or_question_not_blocked(self):
        for msg in ("I have not sent anything yet.", "Do you want me to send it? It is not sent yet.", "If you confirm, it will be sent in a minute.", "I can have it sent whenever you say."):
            code, out, err = self.stop(msg)
            self.assertEqual(code, 0, msg)

    def test_stop_hook_active_never_blocks(self):
        code, out, err = self.stop("I have sent the email.", active=True)
        self.assertEqual(code, 0)

    def test_closing_report_required_with_objective(self):
        self.set_objective()
        self.pre("mcp__gmail__get_message", {"id": "1"})
        self.post("mcp__gmail__get_message", {"id": "1"})
        code, out, err = self.stop("Here is the summary of the email.")
        self.assertEqual(code, 2)
        self.assertIn("Status", err)
        code, out, err = self.stop("Here is the summary.\n\n**Status:** done\nDone: read it")
        self.assertEqual(code, 0)

    def test_no_report_needed_without_tools(self):
        self.set_objective()
        code, out, err = self.stop("Which email do you want me to look at?")
        self.assertEqual(code, 0)


class TestContextHooks(HookTestCase):
    def test_session_start_mentions_persona_and_warns_many_servers(self):
        (self.tmp / ".tanka" / "mcp.json").write_text(json.dumps({"mcpServers": {a: {"type": "http", "url": "https://x/" + a} for a in "abcd"}}))
        (self.tmp / ".tanka" / "persona.json").write_text(json.dumps({"configured": True, "language": "es", "name": "Kira", "user_name": "Juan"}))
        code, out, _ = run_hook("hook_session_start.py", {"session_id": self.sid, "cwd": str(self.tmp), "hook_event_name": "SessionStart", "source": "startup"}, self.tmp)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Kira", ctx)
        self.assertIn("Juan", ctx)
        self.assertIn("4 MCP servers", ctx)

    def test_user_prompt_injects_rules_and_objective(self):
        self.set_objective(title="Triage")
        code, out, _ = run_hook("hook_user_prompt.py", {"session_id": self.sid, "prompt_id": "p9", "cwd": str(self.tmp), "hook_event_name": "UserPromptSubmit", "user_prompt": "hola"}, self.tmp)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Triage", ctx)
        self.assertIn("Never claim", ctx)

    def test_invalid_policy_file_falls_back(self):
        (self.tmp / ".tanka" / "policy.json").write_text("{not json")
        code, out, _ = self.pre("mcp__gmail__trash_message", {"id": "1"})
        self.assertEqual(decision(out), "deny")

    def session_start(self, source):
        _, out, _ = run_hook("hook_session_start.py", {"session_id": self.sid, "cwd": str(self.tmp), "hook_event_name": "SessionStart", "source": source}, self.tmp)
        return out["hookSpecificOutput"]["additionalContext"]

    def test_done_actions_survive_compaction(self):
        args = {"id": "m1", "label": "urgent"}
        self.pre("mcp__gmail__archive_message", args)
        self.post("mcp__gmail__archive_message", args)
        self.pre("mcp__gmail__list_messages", {}, tool_use_id="tu2")
        self.post("mcp__gmail__list_messages", {}, tool_use_id="tu2")
        ctx = self.session_start("compact")
        self.assertIn("may be stale", ctx)
        self.assertIn("mcp__gmail__archive_message (modify)", ctx)
        self.assertNotIn("list_messages", ctx)  # reads change nothing, so they are not in the ledger
        self.assertIn("mcp__gmail__archive_message", self.session_start("resume"))
        self.assertNotIn("may be stale", self.session_start("startup"))


class TestPersonaOnboarding(HookTestCase):
    """First run asks for the language; optional fields can be skipped and
    filled in later, at the moment they matter."""

    def persona(self, **fields):
        base = {"configured": False, "name": "Tanka", "user_name": "", "language": "",
                "tone": "", "personality": "", "output_format": "", "signature": "",
                "timezone": "", "notes": ""}
        base.update(fields)
        (self.tmp / ".tanka" / "persona.json").write_text(json.dumps(base), encoding="utf-8")
        return base

    def session_start(self, source="startup"):
        code, out, _ = run_hook("hook_session_start.py", {
            "session_id": self.sid, "cwd": str(self.tmp),
            "hook_event_name": "SessionStart", "source": source}, self.tmp)
        return out["hookSpecificOutput"]["additionalContext"]

    def user_prompt(self):
        code, out, _ = run_hook("hook_user_prompt.py", {
            "session_id": self.sid, "prompt_id": "p1", "cwd": str(self.tmp),
            "hook_event_name": "UserPromptSubmit", "user_prompt": "hi"}, self.tmp)
        return out["hookSpecificOutput"]["additionalContext"]

    def test_a_new_skill_mid_session_asks_the_user_to_reload(self):
        def prompt(pid):
            code, out, _ = run_hook("hook_user_prompt.py", {
                "session_id": self.sid, "prompt_id": pid, "cwd": str(self.tmp),
                "hook_event_name": "UserPromptSubmit", "user_prompt": "hi"}, self.tmp)
            return out
        self.assertNotIn("systemMessage", prompt("p1"))  # what the session started with
        skill = self.tmp / ".claude" / "skills" / "boards"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: boards\ndescription: x\n---\n")
        self.assertIn("/reload-skills", prompt("p2")["systemMessage"])
        self.assertNotIn("systemMessage", prompt("p3"))  # said once

    def test_template_ships_unconfigured(self):
        shipped = json.loads((self.tmp / ".tanka" / "persona.json").read_text())
        self.assertFalse(shipped.get("configured"))
        self.assertEqual(shipped.get("language"), "")

    def test_first_run_asks_for_language_first(self):
        ctx = self.session_start()
        self.assertIn("FIRST RUN", ctx)
        self.assertIn("which language should you work in", ctx)
        self.assertIn("can be skipped now and filled in later", ctx)

    def test_configured_persona_skips_first_run(self):
        self.persona(configured=True, language="es", name="Kira")
        ctx = self.session_start()
        self.assertNotIn("FIRST RUN", ctx)
        self.assertIn("Work in this language: es", ctx)
        self.assertIn("Kira", ctx)

    def test_language_alone_is_not_configured(self):
        self.persona(language="es")  # configured still False
        self.assertIn("FIRST RUN", self.session_start())

    def test_pending_fields_are_listed_with_hints(self):
        self.persona(configured=True, language="en", user_name="Ada")
        ctx = self.session_start()
        self.assertIn("Profile fields still unset", ctx)
        self.assertIn("signature", ctx)
        self.assertIn("Do not interrogate", ctx)
        self.assertNotIn("user_name (", ctx)  # already set

    def test_complete_persona_has_no_pending_line(self):
        self.persona(configured=True, language="en", user_name="Ada", tone="warm",
                     personality="p", output_format="short", signature="Ada",
                     timezone="UTC", notes="-")
        ctx = self.session_start()
        self.assertNotIn("Profile fields still unset", ctx)

    def test_user_prompt_pushes_setup_when_unconfigured(self):
        ctx = self.user_prompt()
        self.assertIn("SETUP", ctx)
        self.assertIn("working language is still unset", ctx)

    def test_user_prompt_states_language_when_configured(self):
        self.persona(configured=True, language="pt-BR", user_name="Ana")
        ctx = self.user_prompt()
        self.assertIn("Answer in: pt-BR", ctx)
        self.assertIn("Ana", ctx)
        self.assertNotIn("SETUP", ctx)

    def test_send_confirmation_asks_for_missing_signature(self):
        self.persona(configured=True, language="en")
        code, out, _ = self.pre("mcp__gmail__send_message",
                                {"to": "ana@acme.com", "subject": "Hi", "body": "Confirming Thursday at 10, thanks."})
        self.assertEqual(decision(out), "ask")
        self.assertIn("no signature is set", reason(out))

    def test_send_confirmation_quiet_when_signature_set(self):
        self.persona(configured=True, language="en", signature="Ada Lovelace")
        code, out, _ = self.pre("mcp__gmail__send_message",
                                {"to": "ana@acme.com", "subject": "Hi", "body": "Confirming Thursday at 10, thanks."})
        self.assertEqual(decision(out), "ask")
        self.assertNotIn("signature", reason(out))

    def test_draft_tool_flags_missing_signature(self):
        self.persona(configured=True, language="en")
        code, out, _ = self.pre("mcp__gmail__create_draft", {"to": "ana@acme.com", "body": "hello"})
        self.assertEqual(decision(out), "allow")
        self.assertIn("No signature is set", reason(out))


class TestPersonaHelpers(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(REPO / "plugin" / "scripts"))
        import tanka_common
        self.tc = tanka_common

    def test_pending_and_effective(self):
        p = dict(self.tc.DEFAULT_PERSONA)
        self.assertFalse(self.tc.persona_is_configured(p))
        p.update(configured=True, language="en", user_name="Ada")
        self.assertTrue(self.tc.persona_is_configured(p))
        pending = self.tc.pending_persona_fields(p)
        self.assertIn("signature", pending)
        self.assertNotIn("user_name", pending)
        # fallbacks fill behaviour without marking the field as answered
        eff = self.tc.effective_persona(p)
        self.assertTrue(eff["tone"])
        self.assertIn("tone", self.tc.pending_persona_fields(p))

    def test_closing_report_regex_accepts_other_languages(self):
        import re
        rx = self.tc.DEFAULT_POLICY["objective"]["closing_report_regex"]
        for line in ("Status: done", "**Status:** partial", "Estado: completado",
                     "Status: needs-confirmation", "Statut: terminé"):
            self.assertTrue(re.search(rx, line), line)
        self.assertFalse(re.search(rx, "everything went fine"))


class TestReadOutside(HookTestCase):
    """Reading outside the workspace: only from folders the user approved, and a request otherwise."""

    def setUp(self):
        super().setUp()
        self.page = Path(tempfile.mkdtemp(prefix="tanka-page-"))
        self.addCleanup(shutil.rmtree, self.page, True)
        old = os.environ.get("TANKA_PAGE_HOME")
        os.environ["TANKA_PAGE_HOME"] = str(self.page)
        self.addCleanup(lambda: os.environ.pop("TANKA_PAGE_HOME", None) if old is None else os.environ.__setitem__("TANKA_PAGE_HOME", old))
        self.outside = Path(tempfile.mkdtemp(prefix="tanka-outside-")).resolve()
        self.addCleanup(shutil.rmtree, self.outside, True)
        (self.outside / "course" / "submissions").mkdir(parents=True)
        (self.outside / "course" / "key.pdf").write_text("key")

    def approve(self, *dirs):
        pol = self.tmp / ".tanka" / "policy.json"
        data = json.loads(pol.read_text())
        data["read_dirs"] = [str(d) for d in dirs]
        pol.write_text(json.dumps(data))

    def requests(self):
        return tc.access_requests(self.tmp)

    def test_inside_the_workspace_is_not_its_business(self):
        code, out, _ = self.pre("Read", {"file_path": str(self.tmp / ".tanka" / "persona.json")})
        self.assertEqual(decision(out), "")
        self.assertEqual(self.requests(), [])

    def test_outside_and_not_approved_leaves_one_request(self):
        target = self.outside / "course" / "key.pdf"
        code, out, _ = self.pre("Read", {"file_path": str(target)})
        self.assertEqual(decision(out), "deny")
        self.assertIn("Approve button", reason(out))
        self.pre("Read", {"file_path": str(target)}, tool_use_id="tu2")
        reqs = self.requests()
        self.assertEqual(len(reqs), 1)  # asking twice does not ask twice
        self.assertEqual((reqs[0]["dir"], reqs[0]["state"]), (str(self.outside / "course"), "pending"))
        code, out, _ = self.pre("Glob", {"pattern": str(self.outside / "course" / "submissions") + "/**/*.pdf"})
        self.assertEqual(decision(out), "deny")
        self.assertEqual(self.requests()[-1]["dir"], str(self.outside / "course" / "submissions"))

    def test_an_approved_folder_is_read_and_nothing_above_it(self):
        self.approve(self.outside / "course")
        code, out, _ = self.pre("Read", {"file_path": str(self.outside / "course" / "key.pdf")})
        self.assertEqual(decision(out), "allow")
        code, out, _ = self.pre("Grep", {"pattern": "x", "path": str(self.outside / "course" / "submissions")})
        self.assertEqual(decision(out), "allow")
        code, out, _ = self.pre("Read", {"file_path": str(self.outside / "elsewhere.txt")})
        self.assertEqual(decision(out), "deny")
        code, out, _ = self.pre("Write", {"file_path": str(self.outside / "course" / "x.txt"), "content": "x"})
        self.assertEqual(decision(out), "deny")  # read only

    def test_a_link_cannot_lead_out_of_the_workspace(self):
        (self.tmp / "notes").mkdir(exist_ok=True)
        (self.tmp / "notes" / "key.pdf").symlink_to(self.outside / "course" / "key.pdf")
        code, out, _ = self.pre("Read", {"file_path": str(self.tmp / "notes" / "key.pdf")})
        self.assertEqual(decision(out), "deny")

    def test_keys_home_and_tanka_are_never_readable_and_never_asked_for(self):
        self.approve(Path.home(), Path.home() / ".ssh")
        for path in (Path.home() / ".ssh" / "id_rsa", Path.home() / ".tanka" / "workspaces" / "other" / "x",
                     Path.home() / "Documents" / "x"):
            with self.subTest(path=path):
                code, out, _ = self.pre("Read", {"file_path": str(path)})
                self.assertEqual(decision(out), "deny")
        self.assertIn("never allowed", reason(self.pre("Read", {"file_path": str(Path.home() / ".ssh" / "id_rsa")})[1]))
        self.assertTrue(all(".ssh" not in r["dir"] and ".tanka" not in r["dir"] for r in self.requests()))

    def test_the_launcher_passes_each_approved_folder(self):
        self.approve(self.outside / "course", self.outside / "missing", Path.home())
        p = subprocess.run([sys.executable, str(SCRIPTS / "tanka_tools.py"), "read-dirs", str(self.tmp)],
                           capture_output=True, text=True, check=True)
        self.assertEqual(p.stdout.split(), ["--add-dir", str(self.outside / "course")])


if __name__ == "__main__":
    unittest.main()
