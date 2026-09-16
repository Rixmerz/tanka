#!/usr/bin/env python3
"""UserPromptSubmit: reinicia contadores del turno y reinyecta reglas duras,
persona y objetivo activo (mitiga drift de instrucciones en Haiku)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402


def build_context(root, policy, persona, objective, st) -> str:
    lines = [f"[tanka] Recordatorio de turno para {persona.get('name', 'Tanka')}:"]
    for r in policy.get("hard_rules", []):
        lines.append(f"- {r}")
    if objective and objective.get("status") == "active":
        crit = "; ".join(str(c) for c in objective.get("done_when", []))
        lines.append(f"Objetivo activo: «{objective.get('title')}» — {objective.get('goal')}. Hecho cuando: {crit}. "
                     f"Clases permitidas: {', '.join(objective.get('allowed_tool_classes', []))}"
                     + (f". Máx. acciones: {objective['max_tool_calls']}" if objective.get("max_tool_calls") else "")
                     + (". Envío prohibido." if objective.get("may_send") is False else "."))
    else:
        lines.append("Sin objetivo activo. Si la petición requiere más de 3 acciones o cualquier envío, usa /tanka:plan antes de actuar.")
    if persona.get("user_name"):
        lines.append(f"Usuario: {persona['user_name']}. Idioma: {persona.get('language', 'es')}.")
    return "\n".join(lines)


def main() -> None:
    inp = tc.read_input()
    root = tc.workspace_root(inp)
    try:
        st = tc.load_session(root, str(inp.get("session_id", "unknown")))
        tc.ensure_turn(st, inp.get("prompt_id") or f"p{int(__import__('time').time()*1000)}")
        tc.save_session(root, st)
    except Exception:
        st = {}
    policy = tc.load_policy(root)
    persona = tc.load_persona(root)
    objective = tc.load_objective(root)
    tc.emit(tc.additional_context("UserPromptSubmit", build_context(root, policy, persona, objective, st)))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
