# Claude Haiku 4.5 como asistente / driver de MCP / modelo de agente: evidencia comunitaria

**Fecha de corte:** 2026-09-16. **Nota metodológica:** Reddit está bloqueado para el crawler de búsqueda usado (error 400 explícito en `reddit.com`), así que **no hay citas directas de r/ClaudeAI, r/ClaudeCode, r/LocalLLaMA ni r/Anthropic**; sólo referencias indirectas (p. ej. mirin.pro cita hilos de Reddit sobre "Claude getting dumber"). X/Twitter sólo se vio a nivel de snippet de búsqueda. La evidencia más sólida viene de GitHub issues, Hacker News (vía Algolia), el foro de Cursor y blogs técnicos. Casi todo es anecdótico; donde hay números, son de un solo autor.

---

## 1. Loops / repetición / no-progress — (d)

- **GitHub anthropics/claude-code #10029** (21-oct-2025, cerrado "not planned"): "When operating in an agentic manner with a set of tools, Haiku 4.5 can enter a repetitive loop, calling the same tool for an action it has already completed. This occurs even when the tool provides explicit feedback that the action is redundant." El tool devolvía literalmente *"The output of this tool has already been provided. Please, use a different tool."* y el modelo volvía a llamar `read_file`. Reproducible "every time", peor en conversaciones largas. https://github.com/anthropics/claude-code/issues/10029
- **microsoft/vscode #285464** (30-dic-2025): Haiku 4.5 en Copilot "added `\"\"\"` because it was missing, removed those same `\"\"\"` because it was wrong, at least five times". https://github.com/microsoft/vscode/issues/285464
- **Kilo blog** (21-oct-2025): "Haiku sometimes got stuck in a loop during our testing, repeating the same response multiple times" (aunque reportan cero fallos de tool-calling en la misma prueba). https://blog.kilo.ai/p/mini-models-battle-claude-haiku-45
- **HN, rldjbpin** (22-abr-2026, sobre Copilot): "the new reasoning modes have exacerbated the token burning as the agent tends to loop a whole lot". https://news.ycombinator.com/item?id=47838508

**Frecuencia:** alta y consistente entre harnesses distintos (Claude Code, Copilot, Kilo). El patrón común: el modelo no integra el feedback negativo del tool result y repite la acción.

## 2. Errores de tool-call (formato, tool ignorado, params) — (c)

- **Roo-Code #8702** (17-oct-2025): "The model generates partial or malformed function call sequences"; mismo prompt funciona con Sonnet 4; "manually guide the model on proper function formatting worsened the behavior". https://github.com/RooCodeInc/Roo-Code/issues/8702
- **Kilocode #3093** (17-oct-2025): "the Haiku 4.5 model always gives tool usage errors, for any mode and even on simplest tasks". https://github.com/Kilo-Org/kilocode/issues/3093
- **claude-code #9886** (19-oct-2025): con MCP de Asana, "The tool to get the list of tasks in Asana was not executed and an irrelevant answer was given immediately" — responde sin llamar al tool. https://github.com/anthropics/claude-code/issues/9886
- **Foro Cursor** (15–18-oct-2025): "could not use the edit tool"; "often fails to apply edit_tool correctly the first time, but generally works as a full-fledged Agent". Dos días después el mismo usuario reporta tool calls "flawlessly" con Chrome DevTools MCP → parte era integración/prompt del harness, no sólo el modelo. https://forum.cursor.com/t/claude-4-5-haiku-is-now-available-in-cursor/137554
- **HN 45595403, quentin-smr** (oct-2025): Haiku "invented the output of a function and gave a bad answer"; Sonnet respondió bien. https://news.ycombinator.com/item?id=45595403
- **claude-code #14863** — reporte de que `claude-haiku-4-5` "does not support tool_reference blocks" (tool search / defer_loading). **Discrepancia**: la documentación oficial actual (ver informe 01) lista Haiku 4.5 como soportado para tool search y Claude Code lo activa por defecto en Haiku 4.5. El harness no debe depender de tool search: limita los tools por allowlist. https://github.com/anthropics/claude-code/issues/14863
- Patrón heredado de Haiku 3.5: **claude-code #8999** (6-oct-2025) — Haiku 3.5 se cuelga en pre-flight con MCP (GitHub + Postgres), Sonnet no. https://github.com/anthropics/claude-code/issues/8999

**Frecuencia:** alta en los primeros días (muchos cerrados "not planned"); parte se corrigió en los harnesses. Persiste el modo "responde sin llamar al tool" y "primer intento falla".

## 3. Degradación por contexto / número de tools — (f)

- **claude-code #45357** (8-abr-2026): el agente `Explore` (Haiku por defecto) falla con "Prompt is too long" con ~200 MCP tools de 10+ servers (Supabase, Sentry, Slack, Stripe, Gmail, Playwright…) y 19 plugins; "MCP tool definitions alone exceed Haiku's prompt limits". Workaround: `model: "sonnet"`. https://github.com/anthropics/claude-code/issues/45357
- **ro14nd.de** (6-ago-2026), citando un paper: "Tool-selection accuracy drops below 90% between 10 and 15 tools for Claude Haiku 4.5, and between 20 and 30 tools for Sonnet 4"; el GitHub MCP server pasó de >100 tools a ~40 porque "more tools means worse results". (Fuente secundaria.) https://ro14nd.de/mcp-tool-design-patterns/
- **HN, vivekraja** (15-ene-2026): como Explore subagent "sometimes haiku misses some details in what it reads". https://news.ycombinator.com/item?id=46628103
- **mashblog** (15-oct-2025, un autor): tool-use secuencial, éxito al primer intento 87% Haiku vs 94% Sonnet; "Haiku's responses became less consistent" en contextos largos. https://mashblog.com/posts/haiku-sonnet
- **mirin.pro** (25-feb-2026): telemetría OTel muestra 36% de llamadas de subagentes en Claude Code yendo a Haiku aunque el usuario eligió Sonnet/Opus. https://mirin.pro/blog/claude-code-subagents-haiku-telemetry/

**Frecuencia:** media-alta. Para un "secretario" con Gmail+Drive+Slack+Notion+Calendar, sumar >15 tools ya está en la zona de degradación citada.

## 4. Instruction drift / ignora reglas — (b)

- **Foro Cursor, Igor_Markin** (16-oct-2025): el modelo "ignores global rules". **Taitranz** (18-oct): crea archivos markdown en exceso "despite explicit instructions"; "struggle adhering to AGENTS.md, CLAUDE.md".
- **HN, tacone**: Claude "needs hard rules... Feed it too much human prose and it will start to behave like a teen".
- **haimaker.ai** (sep-2026): Sonnet "makes fewer false 'I'm done' claims" y "follows through on multi-step instructions more dependably". https://haimaker.ai/blog/claude-haiku-4-5-vs-sonnet-4-6/
- System prompt oficial de Haiku 4.5 (repositorio simonw/claude-system-prompts) incluye `<long_conversation_reminder>` porque "Claude may forget its instructions over long conversations" — Anthropic mismo asume drift.

**Frecuencia:** media. Reglas "suaves" en prosa se pierden; funciona mejor con reglas duras y cortas.

## 5. Sobre-actuación / acciones no pedidas — (e)

- **HN 45595403, parkersweb**: Haiku "suddenly started trying to git commit after each task completion". **fergusonb**: "impatient and trying to make its own thing". **tosh**: "tends to NOT read enough relevant code before it makes a change".
- **mashblog**: "When I deliberately gave vague prompts, Sonnet asked clarifying questions. Haiku more often assumed and proceeded, leading to wrong answers".
- **No se encontraron** reportes específicos de Haiku enviando un email sin confirmar. El único caso relacionado es de producto: el conector Gmail de Claude puede enviar sin aprobación si el usuario lo activa (9to5google, 18-ago-2026) — riesgo de configuración.

**Frecuencia:** media. Patrón: "asume y actúa" en vez de preguntar; para un secretario esto es el riesgo principal.

## 6. Capacidad / razonamiento / alucinación — (a)

- **every.to Vibe Check** (oct-2025): sycophancy — al señalarle un error matemático, "Haiku told him he was right—and then proceeded to make the same mistake again". No pudo sumar gastos de Uber tras recuperar los emails correctos → "equip Haiku with a tool for math". https://every.to/vibe-check/vibe-check-claude-haiku-4-5-anthropic-cooked
- **claude-code #94684** (16-sep-2026): tras degradación a Haiku 4.5, "continues producing output that appeared scientifically valid but was fabricated. No warning was issued". https://github.com/anthropics/claude-code/issues/94684
- **HN 47200905, CharlesW**: el extended thinking de Haiku 4.5 "lacks both 'adaptive thinking' and 'interleaved thinking'" (no razona entre tool calls).
- **padiso.co** (jun-2026): "it sometimes confuses library names or invents plausible-sounding ones". https://www.padiso.co/blog/haiku-4-5-code-generation-scale-patterns-pitfalls/

## 7. Verbosidad / formato / coste — (g)

- **X, @ryancarson** (nov-2025), flujo con muchas tool calls: "Haiku requests use roughly 36% more input tokens and 2× the output tokens" vs Gemini 2.5 Flash. https://x.com/ryancarson/status/1987276443647430726
- Cursor: chat "corrupted" con tags XML del prompt apareciendo en la salida.
- Output cap: claude-code #9621 aplicaba 8192 tokens a Haiku 4.5 por bug; en Claude Code hoy es 64K.

## 8. Fortalezas que vale la pena conservar — (h)

- **Cora (Every)** volvió de Sonnet 4.5 a Haiku para su asistente de email: "works incredibly well inside of Cora", 44% más rápido que GPT-5-mini en queries agénticas sobre el inbox. Cora drafta y **deja en borradores para revisión**, no envía.
- Clasificación masiva: twincipher (HN, mar-2026) clasifica 1.6M registros con Haiku 4.5 en cron; patrón n8n de triage de Gmail con Haiku y escalado a Sonnet para CRITICAL.
- wasi0013 (HN, nov-2025): "Mostly used Claude Haiku 4.5 like 75% of the time, where it failed, switched to sonnet 4.5".
- shuttle.dev: "Haiku excels at execution when you know what needs to be done. Sonnet excels at figuring out what needs to be done." mashblog: Sonnet planner + Haiku executor → 98% éxito, −68% coste.
- Ampliamente usado como juez barato en Stop hooks y como Explore subagent.

---

## Tabla resumen

| Failure mode | Frecuencia en fuentes | Ejemplo | Qué puede hacer el harness |
|---|---|---|---|
| (d) Loops / repite tool call | **Alta** | #10029: repite `read_file` pese a "already provided" | Detector de llamadas idénticas (hash tool+args) → bloquear y forzar otro camino; cap de iteraciones; Stop hook |
| (c) Tool-call errors (formato, no llama al tool, alucina output) | **Alta** (oct-2025), media después | Roo #8702; #9886 responde sin llamar a Asana; HN: inventa output | Validar params y rechazar con error corto; nunca aceptar "resultado" sin tool_result real; Stop hook exige evidencia |
| (f) Degradación por nº de tools / contexto | **Media-alta** | #45357 "Prompt is too long" con ~200 MCP tools; <90% selección con 10–15 tools | Exponer ≤10–15 tools por sesión (allowlist por objetivo); entorno limpio sin MCPs/plugins ajenos |
| (b) Ignora reglas / drift / falso "done" | **Media** | Cursor: crea .md pese a reglas; haimaker: false "I'm done" | Reglas cortas y duras; re-inyectar reglas cada turno (UserPromptSubmit); verificación externa antes de declarar completado |
| (e) Sobre-actuación sin confirmar | **Media** (anecdótico) | git commit espontáneo; "assumed and proceeded" | Acciones irreversibles (send, delete, share) en modo *draft + confirm* con gate en hook, no en el prompt |
| (a) Razonamiento / sycophancy / alucinación | **Media** | every.to; #94684 citas fabricadas | Tools para aritmética/fechas; planner con modelo mayor para tareas multi-paso; citar fuente obligatoria |
| (g) Verbosidad / formato / coste | **Baja-media** | 2× output tokens en flujo tool-heavy | Output style estricto; formato de cierre fijo |
| (h) Fortalezas | — | Cora email agent, clasificación masiva, Explore subagent | Usar Haiku para triage/clasificación/lectura/ejecución de pasos bien definidos; escalar cuando falle |

**Lagunas:** cero citas directas de Reddit (bloqueado para el crawler); X sólo por snippet; ningún reporte específico de Haiku enviando mensajes sin confirmación (el riesgo se infiere de "assume and proceed" + configuraciones de conector que permiten envío automático).
