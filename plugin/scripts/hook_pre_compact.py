#!/usr/bin/env python3
"""PreCompact: pide que el resumen preserve objetivo, confirmaciones pendientes
y acciones ya realizadas (evita repetir envíos tras compactar)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402


def main() -> None:
    inp = tc.read_input()
    root = tc.workspace_root(inp)
    objective = tc.load_objective(root)
    text = ("[tanka] Al resumir, conserva literalmente: (1) el objetivo activo y sus criterios de hecho, "
            "(2) la lista de acciones YA realizadas con sus resultados (ids, destinatarios, horas), "
            "(3) borradores pendientes de confirmación y (4) preguntas abiertas al usuario.")
    if objective:
        text += f" Objetivo: «{objective.get('title')}» status={objective.get('status')}."
    tc.emit(tc.additional_context("PreCompact", text))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
