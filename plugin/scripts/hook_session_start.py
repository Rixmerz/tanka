#!/usr/bin/env python3
"""SessionStart: carga persona, política, objetivo y avisa de riesgos de
configuración (demasiados servidores MCP, ficheros inválidos)."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402

MAX_RECOMMENDED_SERVERS = 3


def main() -> None:
    inp = tc.read_input()
    root = tc.workspace_root(inp)
    source = inp.get("source", "startup")
    persona = tc.load_persona(root)
    policy = tc.load_policy(root)
    objective = tc.load_objective(root)
    warnings: list[str] = []

    tdir = tc.tanka_dir(root)
    if not tdir.exists():
        warnings.append("No existe .tanka/ en este directorio: estás fuera de un workspace Tanka. Ejecuta `tanka init`.")
    for name in (tc.POLICY_FILE, tc.PERSONA_FILE, tc.MCP_FILE):
        p = tdir / name
        if p.exists():
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                warnings.append(f"{name} no es JSON válido ({exc}); se usan valores por defecto.")
    mcp = tc.load_json(tdir / tc.MCP_FILE, {})
    servers = list((mcp.get("mcpServers") or {}).keys()) if isinstance(mcp, dict) else []
    if len(servers) > MAX_RECOMMENDED_SERVERS:
        warnings.append(f"Hay {len(servers)} servidores MCP habilitados ({', '.join(servers)}). Con Haiku la selección de tool se degrada por encima de ~10-15 tools; deja solo los necesarios para la tarea.")

    lines = [
        f"[tanka] Sesión iniciada ({source}). Eres «{persona.get('name', 'Tanka')}», asistente personal"
        + (f" de {persona['user_name']}" if persona.get("user_name") else "") + ".",
        f"Personalidad: {persona.get('personality', '')}",
        f"Tono: {persona.get('tone', '')}. Formato: {persona.get('output_format', '')}. Idioma: {persona.get('language', 'es')}.",
    ]
    if persona.get("signature"):
        lines.append(f"Firma para correos: {persona['signature']}")
    if persona.get("timezone"):
        lines.append(f"Zona horaria del usuario: {persona['timezone']}")
    if persona.get("notes"):
        lines.append(f"Notas del usuario: {persona['notes']}")
    lines.append("Servidores MCP habilitados: " + (", ".join(servers) if servers else "ninguno (solo conversación y ficheros del workspace)."))
    lines.append("Política: lectura=" + policy["decisions"]["read"] + ", borradores=" + policy["decisions"]["draft"]
                 + ", modificaciones=" + policy["decisions"]["modify"] + ", envíos=" + policy["decisions"]["send"]
                 + ", destructivas=" + policy["decisions"]["destructive"] + ".")
    if objective:
        lines.append(f"Objetivo guardado: «{objective.get('title')}» (status={objective.get('status')}). "
                     + ("Continúa con él o ciérralo con /tanka:plan close." if objective.get("status") == "active" else "Está cerrado; crea uno nuevo con /tanka:plan si hace falta."))
    if source == "compact":
        lines.append("Se ha compactado el contexto: vuelve a leer .tanka/state/objective.json antes de continuar y no repitas acciones ya hechas.")
    lines.append("Skills disponibles: /tanka:setup (persona), /tanka:plan (objetivo), /tanka:draft (correos y mensajes), /tanka:triage (clasificar), /tanka:status (estado).")
    if warnings:
        lines.append("AVISOS: " + " | ".join(warnings))
    tc.emit(tc.additional_context("SessionStart", "\n".join(lines)))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
