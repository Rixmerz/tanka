#!/usr/bin/env python3
"""PreToolUse: política por clase de tool, validación de envíos, control de
objetivo y guardia anti-loop. Fail-closed para escrituras."""
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
        st = tc.load_session(root, session_id)
        tc.ensure_turn(st, prompt_id)
    except Exception as exc:  # harness roto: solo permitimos lecturas
        if tool.startswith("mcp__") or tool in ("Write", "Edit", "MultiEdit", "Bash"):
            tc.pre_tool_decision("deny", f"[tanka] No pude cargar la política ({exc}); bloqueo por seguridad. Avisa al usuario.")
        tc.emit({})
        return

    cls = tc.classify_tool(tool, policy)

    # ---------------- Guardia anti-loop (todas las tools salvo preguntas) ----
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
            reason = (f"[tanka] LOOP: ya llamaste a {tool} con exactamente estos argumentos {identical} veces en este turno. "
                      "El resultado ya está en tu contexto. No repitas la llamada: usa ese resultado, cambia de enfoque o pregunta al usuario.")
        elif st["consecutive_failures"].get(tool, 0) >= int(lg["max_consecutive_failures"]):
            reason = (f"[tanka] {tool} ha fallado {st['consecutive_failures'][tool]} veces seguidas. No lo reintentes. "
                      "Reporta el error al usuario con `Estado: bloqueado` y propone una alternativa.")
        elif same_tool >= int(lg["max_same_tool_per_turn"]):
            reason = (f"[tanka] Has usado {tool} {same_tool} veces en este turno (límite {lg['max_same_tool_per_turn']}). "
                      "Resume lo que tienes y pregunta al usuario cómo seguir.")
        elif total >= budget:
            reason = (f"[tanka] Presupuesto de acciones del turno agotado ({total}/{budget}). "
                      "Detente, resume el progreso con `Estado: parcial` y pide instrucciones.")
        elif int(st.get("session_calls", 0)) >= int(lg["max_calls_per_session"]):
            reason = "[tanka] Presupuesto de acciones de la sesión agotado. Cierra con un resumen y pide al usuario abrir una sesión nueva."
        if reason:
            tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
            tc.save_session(root, st)
            tc.pre_tool_decision("deny", reason)
            return

    # ---------------- Tools built-in ---------------------------------------
    if cls == "builtin":
        if tool in policy["builtin"]["deny"]:
            tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
            tc.save_session(root, st)
            tc.pre_tool_decision("deny", f"[tanka] {tool} está deshabilitado: Tanka es un asistente, no ejecuta código ni comandos. Explica al usuario qué necesitas y por qué.")
            return
        if tool in ("Write", "Edit", "MultiEdit"):
            fp = str(tool_input.get("file_path", "")) if isinstance(tool_input, dict) else ""
            if not tc.path_allowed(root, fp, policy["builtin"]["write_allow_globs"]):
                tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
                tc.save_session(root, st)
                tc.pre_tool_decision("deny", f"[tanka] Escritura no permitida en '{fp}'. Solo puedes escribir en: {', '.join(policy['builtin']['write_allow_globs'])}.")
                return
            if tool == "Write" and fp.replace(os.sep, "/").endswith(".tanka/state/objective.json"):
                try:
                    obj = json.loads(tool_input.get("content", ""))
                except Exception as exc:
                    tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
                    tc.save_session(root, st)
                    tc.pre_tool_decision("deny", f"[tanka] objective.json no es JSON válido: {exc}")
                    return
                errs = tc.validate_objective(obj)
                if errs:
                    tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
                    tc.save_session(root, st)
                    tc.pre_tool_decision("deny", "[tanka] objective.json inválido: " + "; ".join(errs) + ". Campos: title, goal, done_when[], allowed_tool_classes[], status, max_tool_calls?, may_send?, recipient_allowlist?")
                    return
        tc.record_call(st, tool, tool_input, cls, "pending", tool_use_id)
        tc.save_session(root, st)
        tc.emit({})  # flujo de permisos normal
        return

    # ---------------- Tools MCP --------------------------------------------
    decision = policy["decisions"].get(cls, "ask")

    # Objetivo activo: clase permitida y tools explícitas
    if objective and objective.get("status") == "active":
        allowed_classes = objective.get("allowed_tool_classes") or []
        allowed_tools = objective.get("allowed_tools") or []
        if allowed_tools and tool not in allowed_tools:
            decision = "deny"
            why = f"el objetivo '{objective.get('title')}' solo autoriza estas tools: {', '.join(allowed_tools)}"
        elif cls not in allowed_classes and cls != "read":
            decision = "deny"
            why = f"el objetivo '{objective.get('title')}' no autoriza la clase '{cls}' (autorizadas: {', '.join(allowed_classes)})"
        else:
            why = ""
        if decision == "deny" and why:
            tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
            tc.save_session(root, st)
            tc.pre_tool_decision("deny", f"[tanka] {tool} bloqueado: {why}. Si es imprescindible, explica al usuario y pide que amplíe el objetivo.")
            return

    if cls == "destructive" or decision == "deny":
        tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
        tc.save_session(root, st)
        tc.pre_tool_decision("deny", f"[tanka] {tool} es una acción destructiva/irreversible y está bloqueada por política. Propón al usuario la alternativa reversible (archivar, etiquetar, mover a borradores) o pídele que lo haga manualmente.")
        return

    if cls == "send":
        violations, summary = tc.validate_send(tool_input, policy, objective)
        if violations:
            tc.record_call(st, tool, tool_input, cls, "denied", tool_use_id)
            tc.save_session(root, st)
            tc.pre_tool_decision("deny", "[tanka] Envío rechazado por validación: " + "; ".join(violations) + ". Corrige el borrador, muéstralo al usuario y vuelve a intentarlo solo con su confirmación.")
            return
        tc.record_call(st, tool, tool_input, cls, "pending", tool_use_id)
        tc.save_session(root, st)
        tc.pre_tool_decision("ask", "[tanka] Confirmación requerida para ENVIAR con " + tool + ":\n" + tc.fmt_send_summary(summary))
        return

    tc.record_call(st, tool, tool_input, cls, "pending", tool_use_id)
    tc.save_session(root, st)
    if decision == "allow":
        tc.pre_tool_decision("allow", f"[tanka] {cls} permitido")
    else:
        preview = json.dumps(tool_input, ensure_ascii=False)[:300]
        tc.pre_tool_decision("ask", f"[tanka] Confirmación requerida ({cls}) para {tool}: {preview}")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:  # nunca romper la sesión; fail-closed en escrituras
        try:
            tc.pre_tool_decision("deny", f"[tanka] Error interno del hook ({exc}). Acción bloqueada por seguridad; informa al usuario.")
        except Exception:
            sys.exit(0)
