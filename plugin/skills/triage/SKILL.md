---
name: triage
description: Clasifica y prioriza correos, mensajes, tareas o documentos con una rúbrica explícita y nivel de confianza, y aplica etiquetas solo con permiso. Úsalo cuando el usuario diga "revisa mi bandeja", "qué hay urgente", "clasifica", "ordena", "prioriza", "resume lo pendiente".
allowed-tools: Read, Write, Glob
argument-hint: "[qué revisar y con qué criterio]"
---

# Triage con rúbrica

Haiku clasifica bien cuando la rúbrica es explícita y el lote es pequeño. Trabaja en lotes de máximo 15 elementos; si hay más, pide al usuario acotar (fecha, remitente, carpeta).

## 1. Rúbrica
Si el usuario no da criterio, propón y confirma esta rúbrica (una línea por categoría, con ejemplo):

| Categoría | Criterio | Ejemplo |
|---|---|---|
| URGENTE | Pide acción del usuario antes de 24 h o viene de alguien clave | «necesito tu OK hoy» |
| ACCIÓN | Requiere respuesta o tarea sin plazo inmediato | solicitud de reunión |
| INFO | Solo leer; sin acción | newsletter interna, confirmación |
| IGNORAR | Promocional, spam evidente, automático sin valor | ofertas, notificaciones repetidas |

Si `.tanka/persona.json` tiene `notes` con personas o temas prioritarios, úsalos como señal de URGENTE.

## 2. Leer
Usa solo tools de lectura. Para cada elemento anota: remitente, asunto/tema, fecha, 1 línea de por qué la categoría, y **confianza** (alta/media/baja). Confianza baja = pregunta al usuario en vez de decidir.

## 3. Presentar
Tabla con: #, remitente, asunto, categoría, confianza, siguiente paso sugerido (1–5 palabras). Luego una línea de conteo por categoría.

## 4. Actuar (solo si el usuario lo pide o el objetivo lo autoriza)
- Etiquetar/archivar es clase `modify`: el harness pedirá confirmación por lote; explica al usuario que es esperado.
- Nunca borres ni marques spam: propón «archivar» como alternativa.
- Para los URGENTE, ofrece preparar borradores con `/tanka:draft`; no envíes nada desde triage.

## 5. Cerrar
```
Estado: completado | parcial
Hecho: N clasificados, M etiquetados
Pendiente: K con confianza baja esperan tu decisión
```
