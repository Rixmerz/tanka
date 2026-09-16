#!/usr/bin/env python3
"""PostToolUseFailure: registra el fallo; a partir del segundo fallo del mismo
tool instruye a parar y reportar en vez de reintentar a ciegas."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402


def main() -> None:
    inp = tc.read_input()
    root = tc.workspace_root(inp)
    tool = str(inp.get("tool_name", ""))
    n = 0
    try:
        st = tc.load_session(root, str(inp.get("session_id", "unknown")))
        tc.ensure_turn(st, inp.get("prompt_id"))
        tc.set_outcome(st, inp.get("tool_use_id"), tool, inp.get("tool_input", {}), "error")
        n = int(st["consecutive_failures"].get(tool, 0))
        tc.save_session(root, st)
    except Exception:
        pass
    policy = tc.load_policy(root)
    limit = int(policy["loop_guard"]["max_consecutive_failures"])
    err = str(inp.get("error", ""))[:300]
    if n >= limit - 1:
        msg = (f"[tanka] {tool} ha fallado {n} veces seguidas ({err}). NO lo reintentes con los mismos argumentos. "
               "Si no hay una alternativa clara, reporta al usuario con `Estado: bloqueado` y el error literal.")
    else:
        msg = f"[tanka] {tool} falló: {err}. Lee el error antes de reintentar; si el motivo es un dato que falta, pregunta al usuario."
    tc.emit(tc.additional_context("PostToolUseFailure", msg))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
