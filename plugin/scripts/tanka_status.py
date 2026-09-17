#!/usr/bin/env python3
"""Print the workspace state. Used by `tanka doctor`."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402


def main() -> None:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or (sys.argv[1] if len(sys.argv) > 1 else os.getcwd()))
    persona_raw = tc.load_persona(root)
    persona = tc.effective_persona(persona_raw)
    policy = tc.load_policy(root)
    objective = tc.load_objective(root)
    mcp = tc.load_json(tc.tanka_dir(root) / tc.MCP_FILE, {})
    servers = list((mcp.get("mcpServers") or {}).keys()) if isinstance(mcp, dict) else []
    print(f"Workspace: {root}")
    if not tc.persona_is_configured(persona_raw):
        print("Profile:   NOT SET UP — the first session will ask for the working language")
    else:
        print(f"Assistant: {persona.get('name')} | user: {persona.get('user_name') or '-'} | language: {persona.get('language')}")
        pending = tc.pending_persona_fields(persona_raw)
        print("Pending:   " + (", ".join(pending) if pending else "nothing, the profile is complete"))
    print(f"MCP enabled ({len(servers)}): {', '.join(servers) or 'none'}")
    print("Decisions: " + ", ".join(f"{k}={v}" for k, v in policy["decisions"].items()))
    lg = policy["loop_guard"]
    print(f"Loop guard: identical/turn={lg['max_identical_calls_per_turn']}, actions/turn={lg['max_calls_per_turn']}, consecutive failures={lg['max_consecutive_failures']}")
    if objective:
        print("Objective: " + json.dumps(objective, ensure_ascii=False, indent=2))
    else:
        print("Objective: none")
    sessions = sorted((tc.state_dir(root) / tc.SESSION_STATE_DIR).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    for s in sessions:
        st = tc.load_json(s, {})
        calls = st.get("turn", {}).get("calls", [])
        print(f"Session {s.stem[:12]}...: {st.get('session_calls', 0)} actions, {st.get('denials', 0)} blocked, last turn {len(calls)} calls")


if __name__ == "__main__":
    main()
