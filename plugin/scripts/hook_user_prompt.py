#!/usr/bin/env python3
"""UserPromptSubmit: reset the per-turn counters and re-inject the hard rules,
the persona and the active objective. This is what keeps a small model from
drifting away from its instructions as the conversation grows."""
from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402


def build_context(policy: dict, persona_raw: dict, objective: dict | None) -> str:
    persona = tc.effective_persona(persona_raw)
    lines = [f"Turn reminder for {persona.get('name', 'Tanka')}:"]
    for r in policy.get("hard_rules", []):
        lines.append(f"- {r}")

    if not tc.persona_is_configured(persona_raw):
        lines.append("SETUP: the working language is still unset. Ask for it first, in one short line, save it to .tanka/persona.json with \"configured\": true, then answer the user's request.")
    else:
        lines.append(f"Answer in: {persona['language']}.")
        if persona.get("user_name"):
            lines.append(f"User: {persona['user_name']}.")
        pending = tc.pending_persona_fields(persona_raw)
        if pending:
            lines.append("Unset profile fields: " + ", ".join(pending) + ". Ask for one only when this turn actually needs it.")

    if objective and objective.get("status") == "active":
        crit = "; ".join(str(c) for c in objective.get("done_when", []))
        lines.append(f"Active objective: \"{objective.get('title')}\" - {objective.get('goal')}. Done when: {crit}. "
                     f"Allowed classes: {', '.join(objective.get('allowed_tool_classes', []))}"
                     + (f". Max actions: {objective['max_tool_calls']}" if objective.get("max_tool_calls") else "")
                     + (". Sending is forbidden." if objective.get("may_send") is False else "."))
    else:
        lines.append("No active objective. If this request needs more than three actions, or any send, run /tanka:plan before acting.")
    return "\n".join(lines)


def main() -> None:
    inp = tc.read_input()
    root = tc.workspace_root(inp)
    try:
        st = tc.load_session(root, str(inp.get("session_id", "unknown")))
        tc.ensure_turn(st, inp.get("prompt_id") or f"p{int(time.time() * 1000)}")
        tc.save_session(root, st)
    except Exception:
        pass
    policy = tc.load_policy(root)
    persona = tc.load_persona(root)
    objective = tc.load_objective(root)
    tc.emit(tc.additional_context("UserPromptSubmit", build_context(policy, persona, objective)))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
