#!/usr/bin/env python3
"""PreToolUse: per-class policy, outgoing-message validation, objective scope
and loop guard. Fails closed for anything that writes."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402


def main() -> None:
    inp = tc.read_input()
    tool = str(inp.get("tool_name", ""))
    tool_input = inp.get("tool_input", {})
    session_id = str(inp.get("session_id", "unknown"))
    prompt_id = inp.get("prompt_id")
    tool_use_id = inp.get("tool_use_id")
    root = tc.workspace_root(inp)

    try:
        policy = tc.load_policy(root)
        objective = tc.load_objective(root)
        persona = tc.load_persona(root)
        st = tc.load_session(root, session_id)
        tc.ensure_turn(st, prompt_id)
    except Exception as exc:  # broken harness: reads only
        if tool.startswith("mcp__") or tool in ("Write", "Edit", "MultiEdit", "Bash"):
            tc.pre_tool_decision("deny", f"Tanka could not load its policy ({exc}); blocking this action for safety. Tell the user.")
        tc.emit({})
        return

    cls = tc.classify_tool(tool, policy)

    # ---------------- Loop guard (every tool except questions) --------------
    lg = policy["loop_guard"]
    calls = st["turn"]["calls"]
    if tool not in ("AskUserQuestion",):
        h = tc.input_hash(tool, tool_input)
        identical = sum(1 for c in calls if c["hash"] == h and c["outcome"] != "denied")
        same_tool = sum(1 for c in calls if c["tool"] == tool and c["outcome"] != "denied")
        total = sum(1 for c in calls if c["outcome"] != "denied")
        budget = int(lg["max_calls_per_turn"])
        if objective and objective.get("status") == "active" and objective.get("max_tool_calls"):
            budget = min(budget, int(objective["max_tool_calls"]))
        reason = None
        if identical >= int(lg["max_identical_calls_per_turn"]):
            reason = (f"LOOP: you already called {tool} with exactly these arguments {identical} times this turn. "
                      "Its result is already in your context. Do not repeat the call: use that result, change approach, or ask the user.")
        elif st["consecutive_failures"].get(tool, 0) >= int(lg["max_consecutive_failures"]):
            reason = (f"{tool} has failed {st['consecutive_failures'][tool]} times in a row. Do not retry it. "
                      "Report to the user with `Status: blocked` and propose an alternative.")
        elif same_tool >= int(lg["max_same_tool_per_turn"]):
            reason = (f"You have used {tool} {same_tool} times this turn (limit {lg['max_same_tool_per_turn']}). "
                      "Summarise what you have and ask the user how to proceed.")
        elif total >= budget:
            reason = (f"Action budget for this turn is spent ({total}/{budget}). "
                      "Stop, summarise progress with `Status: partial` and ask for instructions.")
        elif int(st.get("session_calls", 0)) >= int(lg["max_calls_per_session"]):
            reason = "Action budget for this session is spent. Close with a summary and ask the user to start a new session."
        if reason:
            tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
            tc.save_session(root, st)
            tc.pre_tool_decision("deny", reason)
            return

    # ---------------- Built-in tools ---------------------------------------
    if cls == "builtin":
        if tool in policy["builtin"]["deny"]:
            tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
            tc.save_session(root, st)
            tc.pre_tool_decision("deny", f"{tool} is disabled: Tanka is an assistant and does not run code or commands. Tell the user what you need and why.")
            return
        if tool in ("Write", "Edit", "MultiEdit"):
            fp = str(tool_input.get("file_path", "")) if isinstance(tool_input, dict) else ""
            if not tc.path_allowed(root, fp, policy["builtin"]["write_allow_globs"]):
                tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
                tc.save_session(root, st)
                tc.pre_tool_decision("deny", f"Writing to '{fp}' is not allowed. You may only write to: {', '.join(policy['builtin']['write_allow_globs'])}.")
                return
            if tool == "Write" and fp.replace(os.sep, "/").endswith(".tanka/state/objective.json"):
                try:
                    obj = json.loads(tool_input.get("content", ""))
                except Exception as exc:
                    tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
                    tc.save_session(root, st)
                    tc.pre_tool_decision("deny", f"objective.json is not valid JSON: {exc}")
                    return
                errs = tc.validate_objective(obj)
                if errs:
                    tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
                    tc.save_session(root, st)
                    tc.pre_tool_decision("deny", "objective.json is invalid: " + "; ".join(errs) + ". Fields: title, goal, done_when[], allowed_tool_classes[], status, max_tool_calls?, may_send?, recipient_allowlist?")
                    return
        tc.record_call(st, tool, tool_input, cls, "pending", tool_use_id)
        tc.save_session(root, st)
        if tool in ("Write", "Edit", "MultiEdit"):
            # The path already passed the allowlist above: these are the
            # assistant's own working files inside the workspace, so grant them
            # here instead of falling through to a permission prompt. That
            # keeps the profile and the objective writable in a headless run.
            tc.pre_tool_decision("allow", f"{tool} allowed inside the workspace ({fp})")
        tc.emit({})  # every other built-in follows the normal permission flow
        return

    # ---------------- MCP tools --------------------------------------------
    decision = policy["decisions"].get(cls, "ask")

    # Active objective: allowed classes and explicit tool list
    if objective and objective.get("status") == "active":
        allowed_classes = objective.get("allowed_tool_classes") or []
        allowed_tools = objective.get("allowed_tools") or []
        why = ""
        if allowed_tools and tool not in allowed_tools:
            decision = "deny"
            why = f"the objective \"{objective.get('title')}\" only authorises these tools: {', '.join(allowed_tools)}"
        elif cls not in allowed_classes and cls != "read":
            decision = "deny"
            why = f"the objective \"{objective.get('title')}\" does not authorise the '{cls}' class (authorised: {', '.join(allowed_classes)})"
        if decision == "deny" and why:
            tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
            tc.save_session(root, st)
            tc.pre_tool_decision("deny", f"{tool} is blocked: {why}. If it is essential, explain it to the user and ask them to widen the objective.")
            return

    if cls == "destructive" or decision == "deny":
        tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
        tc.save_session(root, st)
        tc.pre_tool_decision("deny", f"{tool} is a destructive or irreversible action and is blocked by policy. Offer the user the reversible alternative (archive, label, move to drafts) or ask them to do it themselves.")
        return

    if cls == "send":
        violations, summary = tc.validate_send(tool_input, policy, objective)
        if violations:
            tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
            tc.save_session(root, st)
            tc.pre_tool_decision("deny", "Send rejected by validation: " + "; ".join(violations) + ". Fix the draft, show it to the user, and only try again with their confirmation.")
            return
        tc.record_call(st, tool, tool_input, cls, "pending", tool_use_id)
        tc.save_session(root, st)
        reason = "Confirmation required to SEND with " + tool + ":\n" + tc.fmt_send_summary(summary)
        if not str(persona.get("signature", "")).strip():
            reason += "\nNote: no signature is set in .tanka/persona.json. Ask the user how they want to sign, once, and offer to save it."
        tc.pre_tool_decision("ask", reason)
        return

    tc.record_call(st, tool, tool_input, cls, "pending", tool_use_id)
    tc.save_session(root, st)
    if decision == "allow":
        reason = f"{cls} allowed"
        if cls == "draft" and not str(persona.get("signature", "")).strip():
            reason += ". No signature is set in .tanka/persona.json; ask the user how to sign before the draft goes out, and offer to save it."
        tc.pre_tool_decision("allow", reason)
    else:
        preview = json.dumps(tool_input, ensure_ascii=False)[:300]
        tc.pre_tool_decision("ask", f"Confirmation required ({cls}) for {tool}: {preview}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # never break the session; fail closed on writes
        try:
            tc.pre_tool_decision("deny", f"Tanka hook error ({exc}). Action blocked for safety; tell the user.")
        except Exception:
            sys.exit(0)
