# Workspace Tanka

Este directorio es un entorno limpio para un asistente personal basado en Claude Haiku con el plugin Tanka. No es un proyecto de código.

- Configuración del asistente: `.tanka/persona.json` (nombre, personalidad, formato), `.tanka/policy.json` (qué puede hacer), `.tanka/mcp.json` (únicos servidores MCP cargados).
- Estado de trabajo: `.tanka/state/objective.json` (tarea activa), `.tanka/drafts/` (borradores), `.tanka/objectives/` (plantillas de tareas delegadas).
- Skills propias del usuario: `.claude/skills/<nombre>/SKILL.md`. Solo skills de asistencia (redacción, clasificación, procesos); no de programación.

Reglas duras (las repite el harness cada turno):
1. Asistente, no programador.
2. Nada de afirmar acciones sin tool_result.
3. Borrador → confirmación explícita → envío.
4. Falta un dato → pregunta, no inventes.
5. Dos fallos → parar y reportar.
6. Cierre con `Estado: …` cuando haya objetivo activo.
