---
name: status
description: Muestra el estado del workspace Tanka (persona, servidores MCP habilitados, política, objetivo activo). Úsalo cuando el usuario pregunte "cómo estás configurado", "qué puedes hacer", "estado", "qué objetivo tienes".
allowed-tools: Read, Glob
---

# Estado del workspace

Lee estos ficheros (los que existan) con la tool Read, sin usar Bash:

1. `.tanka/persona.json` — quién eres y para quién trabajas.
2. `.tanka/mcp.json` — servidores MCP habilitados (clave `mcpServers`).
3. `.tanka/policy.json` — decisiones por clase (`decisions`) y límites anti-loop (`loop_guard`).
4. `.tanka/state/objective.json` — objetivo activo (puede no existir).
5. `.tanka/objectives/*.json` (con Glob) — plantillas de tareas delegadas disponibles.

Resume en lenguaje natural, en este orden y sin jerga:

- **Quién soy**: nombre, para quién trabajo, idioma y tono.
- **Qué puedo usar**: servidores MCP habilitados (si son más de 3, avisa de que con Haiku conviene reducirlos).
- **Qué está permitido**: lectura y borradores libres; modificaciones y envíos con confirmación; nada destructivo; sin código.
- **Objetivo activo**: título, meta, criterios de hecho y estado; o «ninguno» y las plantillas disponibles.
- **Límites**: llamadas idénticas por turno, acciones por turno, fallos seguidos.

No propongas cambiar la política desde la conversación: eso se edita en `.tanka/policy.json` fuera de la sesión.
