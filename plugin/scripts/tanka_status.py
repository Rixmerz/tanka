#!/usr/bin/env python3
"""Imprime el estado del workspace (usado por /tanka:status y `tanka doctor`)."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402


def main() -> None:
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or (sys.argv[1] if len(sys.argv) > 1 else os.getcwd()))
    persona = tc.load_persona(root)
    policy = tc.load_policy(root)
    objective = tc.load_objective(root)
    mcp = tc.load_json(tc.tanka_dir(root) / tc.MCP_FILE, {})
    servers = list((mcp.get("mcpServers") or {}).keys()) if isinstance(mcp, dict) else []
    print(f"Workspace: {root}")
    print(f"Asistente: {persona.get('name')} | usuario: {persona.get('user_name') or '-'} | idioma: {persona.get('language')}")
    print(f"MCP habilitados ({len(servers)}): {', '.join(servers) or 'ninguno'}")
    print("Decisiones: " + ", ".join(f"{k}={v}" for k, v in policy["decisions"].items()))
    lg = policy["loop_guard"]
    print(f"Anti-loop: idénticas/turno={lg['max_identical_calls_per_turn']}, acciones/turno={lg['max_calls_per_turn']}, fallos seguidos={lg['max_consecutive_failures']}")
    if objective:
        print("Objetivo: " + json.dumps(objective, ensure_ascii=False, indent=2))
    else:
        print("Objetivo: ninguno")
    sessions = sorted((tc.state_dir(root) / tc.SESSION_STATE_DIR).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    for s in sessions:
        st = tc.load_json(s, {})
        calls = st.get("turn", {}).get("calls", [])
        print(f"Sesión {s.stem[:12]}…: {st.get('session_calls', 0)} acciones, {st.get('denials', 0)} bloqueos, último turno {len(calls)} llamadas")


if __name__ == "__main__":
    main()
