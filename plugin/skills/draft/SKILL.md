---
name: draft
description: Redacta, revisa y (solo con confirmación) envía correos, respuestas y mensajes en nombre del usuario. Úsalo cuando el usuario diga "escribe un correo", "responde a", "contesta este mensaje", "redacta", "manda un mensaje", "reenvía", o pida un borrador.
allowed-tools: Read, Write, Glob, Agent
argument-hint: "[a quién y sobre qué]"
---

# Borrador → verificación → confirmación → envío

Regla fija: el usuario ve el mensaje completo antes de que salga. Nunca envías «para ahorrar tiempo».

## 1. Recoger contexto (solo lectura)
- Si es una respuesta, lee el hilo original con las tools de lectura disponibles. Cita en tu cabeza los datos exactos: nombres, fechas, importes, lo que se pide.
- Lee `.tanka/persona.json` para idioma, tono y firma.
- Si faltan destinatario, asunto, intención o un dato clave, pregunta UNA vez con opciones. No inventes ni pongas placeholders.

## 2. Redactar
Presenta el borrador así, literal, en bloque de cita:

> **Para:** …
> **CC:** … (si aplica)
> **Asunto:** …
>
> Cuerpo…
>
> Firma

Checklist antes de mostrarlo (corrígelo tú mismo si falla algo):
- [ ] Responde a lo que se pidió, ni más ni menos.
- [ ] Sin `[NOMBRE]`, `TODO`, `{{…}}`, «insertar aquí».
- [ ] Datos (fechas, cifras, nombres) provienen de la petición o de lo leído; nada inventado.
- [ ] Idioma, tono y firma de la persona.
- [ ] Sin datos sensibles innecesarios ni credenciales.
- [ ] Longitud adecuada: un correo de trabajo normal cabe en 5–10 líneas.

Guarda una copia en `.tanka/drafts/<fecha>-<slug>.md` (útil para auditoría y para reanudar tras un corte).

## 3. Verificar (obligatorio si va a enviarse a alguien externo o si el objetivo lo pide)
Lanza el subagente `tanka-verifier` pasándole el borrador y la petición original. Si devuelve FAIL, aplica los cambios mínimos y vuelve a mostrar el borrador. No preguntes al usuario hasta tener un PASS o dos intentos.

## 4. Confirmar
Pregunta exactamente: «¿Lo envío tal cual? (sí / cambia X / no)». Solo un «sí» claro autoriza. «Vale, pero…» no es un sí: aplica el cambio y vuelve a preguntar.

## 5. Enviar
- Usa la tool de envío del servidor MCP disponible con los MISMOS datos del borrador mostrado. El harness pedirá una confirmación adicional al usuario; es normal.
- Si el harness rechaza el envío, lee el motivo (placeholder, destinatario bloqueado, objetivo sin `may_send`), corrige y repite desde el paso 2. No intentes otra tool para «saltarte» el bloqueo.
- Si no hay ninguna tool de envío disponible, di claramente que solo puedes dejar el borrador y ofrécelo guardado en `.tanka/drafts/` (o como borrador de Gmail si existe esa tool).

## 6. Cerrar
Reporta con el dato real devuelto por el tool (id del mensaje, hora). Formato:

```
Estado: completado
Hecho: enviado a … (id …)
Pendiente: nada
```

Si el usuario dijo «no» o «luego», el estado es `necesita confirmación` y el borrador queda guardado.
