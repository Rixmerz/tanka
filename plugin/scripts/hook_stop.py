#!/usr/bin/env python3
"""Stop: verificación de cierre.

1. Afirmaciones sin evidencia: si el mensaje final dice "enviado/borrado/
   etiquetado" pero en este turno no hubo tool_result exitoso de esa clase,
   se bloquea el cierre una vez y se exige corregir.
2. Informe de cierre: con objetivo activo y tools usadas en el turno, el
   mensaje debe terminar con `Estado: ...`.
Nunca bloquea dos veces seguidas (stop_hook_active)."""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402


def main() -> None:
    inp = tc.read_input()
    if inp.get("stop_hook_active"):
        tc.emit({})
        return
    root = tc.workspace_root(inp)
    policy = tc.load_policy(root)
    objective = tc.load_objective(root)
    msg = str(inp.get("last_assistant_message", "") or "")
    try:
        st = tc.load_session(root, str(inp.get("session_id", "unknown")))
        tc.ensure_turn(st, inp.get("prompt_id"))
        calls = st["turn"]["calls"]
    except Exception:
        calls = []

    ok_classes = {c["class"] for c in calls if c.get("outcome") == "ok"}
    problems: list[str] = []

    # 1) Afirmaciones sin evidencia
    claims = policy["objective"].get("claim_patterns", {})
    satisfied = {"send": {"send"}, "modify": {"modify", "draft", "send"}, "destructive": {"destructive"}}
    negation = re.compile(r"(?i)(\bno\b|\bnot\b|n't\b|\bnunca\b|\bsin\b|\bsi\b|\bif\b|\bantes de\b|\bbefore\b|\bpuedo\b|\bpodr[ií]a\b|\bcan\b|\bcould\b|\bquieres\b|\bwant\b|\bwould\b|\bready\b|\blist[oa] para\b|\bcuando\b|\bwhen\b|\bonce\b|¿|\?)")
    for cls, pats in claims.items():
        if ok_classes & satisfied.get(cls, {cls}):
            continue
        for pat in pats:
            try:
                m = re.search(pat, msg)
            except re.error:
                continue
            if not m:
                continue
            # Contexto previo en la misma frase: negaciones, condicionales o preguntas no cuentan.
            start = max(msg.rfind(".", 0, m.start()), msg.rfind("\n", 0, m.start()), msg.rfind("!", 0, m.start())) + 1
            before = msg[start:m.start()]
            after = msg[m.end():m.end() + 40]
            if negation.search(before) or "?" in after.split("\n")[0]:
                continue
            problems.append(f"Tu mensaje afirma una acción de tipo '{cls}' («{m.group(0)}») pero en este turno no hay ningún tool_result exitoso de esa clase. "
                            "Corrige el mensaje: di exactamente qué se hizo y qué no, o realiza la acción (con confirmación) antes de afirmarla.")
            break

    # 2) Informe de cierre con objetivo activo
    if objective and objective.get("status") == "active" and policy["objective"].get("require_closing_report_when_tools_used"):
        used = [c for c in calls if c.get("outcome") in ("ok", "error")]
        if used and not re.search(policy["objective"]["closing_report_regex"], msg):
            problems.append("Hay un objetivo activo y usaste tools en este turno, pero el mensaje no termina con la línea de cierre. "
                            "Añade al final: `Estado: completado | parcial | bloqueado | necesita confirmación` seguido de una línea con lo hecho y lo pendiente. "
                            "Si el objetivo está completo, actualiza .tanka/state/objective.json con status=done.")

    if problems:
        tc.block_with_stderr("[tanka] Cierre bloqueado:\n- " + "\n- ".join(problems))
    tc.emit({})


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
