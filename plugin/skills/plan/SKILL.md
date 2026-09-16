---
name: plan
description: Define, revisa o cierra el objetivo de una tarea delegada antes de actuar (criterios de hecho, tools permitidas, límite de acciones, si se puede enviar). Úsalo antes de cualquier tarea con más de 3 acciones o con envíos, cuando el usuario diga "encárgate de", "haz esto por mí", "tarea", "plan", o para cerrar con "plan close".
allowed-tools: Read, Write, Glob
argument-hint: "[descripción de la tarea | close | show]"
---

# Objetivo de tarea

El objetivo vive en `.tanka/state/objective.json`. El harness lo usa para bloquear tools fuera de alcance, limitar acciones y exigir un cierre verificable. Un objetivo mal definido = una tarea que no se puede validar. Sé concreto.

## Si `$ARGUMENTS` es `show`
Lee el fichero y resúmelo en 5 líneas (título, meta, criterios, permisos, estado). Fin.

## Si `$ARGUMENTS` es `close`
Lee el fichero, pregunta al usuario si el resultado es `done`, `blocked` o `cancelled` si no está claro, actualiza `status` y añade `closed_at` (ISO) y `outcome` (1 frase). Termina con `Estado: completado` (o el que corresponda). Fin.

## En cualquier otro caso: crear objetivo

1. Si ya existe un objetivo con `status: active`, muéstralo y pregunta si lo reemplaza o continúa. No pises un objetivo activo sin permiso.
2. Con la descripción (`$ARGUMENTS` o la última petición del usuario), redacta el objetivo. Si falta algo esencial (a quién, qué, cuándo, qué se considera terminado), haz UNA pregunta con opciones. Máximo dos preguntas en total.
3. Elige las clases de tool mínimas:
   - `read`: leer correos, ficheros, calendario, buscar.
   - `draft`: crear borradores (no envía).
   - `modify`: etiquetar, archivar, mover, actualizar (reversible).
   - `send`: enviar, responder, reenviar, publicar, invitar (irreversible → siempre confirmación).
   - `destructive` nunca se autoriza desde un objetivo.
4. Escribe el fichero con esta forma (el harness lo valida y rechaza campos inválidos):

```json
{
  "id": "2026-09-16-triage-inbox",
  "title": "Triage del inbox de hoy",
  "goal": "Clasificar los correos no leídos de hoy en urgente/normal/ignorar y preparar respuestas para los urgentes",
  "done_when": [
    "Todos los no leídos de hoy tienen etiqueta",
    "Cada urgente tiene un borrador de respuesta guardado en Gmail",
    "Se ha presentado al usuario una tabla resumen"
  ],
  "allowed_tool_classes": ["read", "draft", "modify"],
  "allowed_tools": [],
  "may_send": false,
  "recipient_allowlist": [],
  "max_tool_calls": 30,
  "status": "active",
  "created_at": "2026-09-16T09:00:00Z",
  "notes": ""
}
```

   - `allowed_tools` vacío = cualquier tool de las clases permitidas; si lo rellenas, solo esas (nombres `mcp__servidor__tool`).
   - `may_send: true` solo si el usuario ha dicho explícitamente que quiere que se envíe algo en esta tarea.
   - `max_tool_calls`: estimación realista × 2, nunca más de 60.

5. Muestra el objetivo en 4–6 líneas y pide un «ok». Solo entonces empieza a ejecutar, paso a paso, comprobando cada criterio de `done_when`.

## Durante la ejecución
- Antes de cada acción, comprueba mentalmente: ¿está dentro de la clase permitida? ¿ya la hice? ¿tengo todos los datos?
- Si el harness bloquea una acción, no la reintentes igual: lee el motivo, corrige o pregunta.
- Al terminar o quedarte bloqueado, ejecuta `/tanka:plan close` y cierra con la línea `Estado: …`.
