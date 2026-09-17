"""Tanka hook tests, driving each hook with the JSON event Claude Code sends."""
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
SCRIPTS = REPO / "plugin" / "scripts"
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
        pol["loop_guard"]["max_calls_per_turn"] = 3
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

    def test_pre_compact(self):
        code, out, _ = run_hook("hook_pre_compact.py", {"session_id": self.sid, "cwd": str(self.tmp), "hook_event_name": "PreCompact", "source": "auto"}, self.tmp)
        self.assertIn("keep verbatim", out["hookSpecificOutput"]["additionalContext"])


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


if __name__ == "__main__":
    unittest.main()
