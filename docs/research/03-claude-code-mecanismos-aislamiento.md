# Mecanismos de Claude Code para un entorno limpio, guardrails y personalización

Fuente principal: documentación oficial en https://code.claude.com/docs/en/ (settings, settings-reference, model-config, hooks, hooks-guide, plugins, plugins-reference, skills, sub-agents, permission-modes, auto-mode-config, sandboxing, memory, cli-reference, headless, agent-sdk). Verificado además contra `claude --help` de la CLI 2.1.273 el 2026-09-16 (marcado como **[CLI]**).

## 1. Ámbitos de settings y aislamiento

Precedencia (mayor a menor): managed → `--settings` (CLI) → `.claude/settings.local.json` → `.claude/settings.json` → `~/.claude/settings.json`. Arrays y objetos se **fusionan** entre capas, así que un proyecto no "borra" lo que el usuario tiene a nivel global: solo puede sobreescribir claves escalares o denegar explícitamente.

Lo que sí garantiza aislamiento real es la CLI:

| Flag **[CLI]** | Efecto |
|---|---|
| `--setting-sources project,local` | Ignora `~/.claude/settings.json` (hooks, permisos, plugins habilitados a nivel usuario). |
| `--strict-mcp-config --mcp-config <archivo>` | "Only use MCP servers from --mcp-config, ignoring all other MCP configurations". Es la única forma de excluir los MCP de `~/.claude.json` y de plugins ajenos. |
| `--plugin-dir <ruta>` | Carga un plugin "for this session only" sin instalarlo. |
| `--restricted` | Quita Bash/PowerShell/REPL y WebFetch salvo que `--tools` los nombre, ignora settings user/project/local (managed y `--settings` sí aplican), confina los file tools al working dir, rechaza `bypassPermissions`. |
| `--safe-mode` | Desactiva TODAS las customizaciones (incluido nuestro plugin); sirve para diagnosticar, no para operar. |
| `--bare` | Salta hooks, plugin sync, auto-memory, CLAUDE.md autodiscovery; conserva `--plugin-dir`, `--settings`, `--mcp-config`. Útil para modo headless, pero "skip hooks" lo hace incompatible con los guardrails por hook. |
| `--disallowedTools`, `--allowedTools`, `--tools` | Recorte de tools built-in por sesión. |
| `--model haiku`, `--effort`, `--max-turns`, `--max-budget-usd` (solo `-p`), `--permission-mode`, `--permission-prompts none` (solo `-p`) | Modelo, límites y permisos por sesión. |

Claves de settings relevantes: `permissions.allow/deny/ask/defaultMode`, `permissions.disableBypassPermissionsMode`, `enabledPlugins` (`{}` o `{"x": false}` a nivel proyecto), `disabledMcpjsonServers`, `enabledMcpjsonServers`, `deniedMcpServers`, `claudeMdExcludes` (puede excluir `~/.claude/CLAUDE.md` por glob; no el managed), `disableBundledSkills`, `skillOverrides`, `hooks`, `model`, `effortLevel`, `sandbox.*`, `autoMode.*`.

Conclusión: el "clean room" se consigue con un **launcher** que combine `--setting-sources project,local` + `--strict-mcp-config --mcp-config` + `--plugin-dir` + `--disallowedTools`, y una `.claude/settings.json` de proyecto con `enabledPlugins: {}` y `claudeMdExcludes` como segunda capa.

## 2. Modelo

- `model: "haiku"` en settings, `--model haiku`, `/model haiku`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL` (a qué resuelve el alias), `CLAUDE_CODE_SUBAGENT_MODEL`.
- Subagentes: `model: haiku|sonnet|opus|inherit` en frontmatter.
- `effortLevel` / `--effort` existe en Claude Code **[CLI]**; según la API, Haiku 4.5 no soporta `effort` (ver informe 01), por lo que no hay que depender de él.

## 3. Hooks

Eventos: `SessionStart`, `SessionEnd`, `Setup`, `UserPromptSubmit`, `Stop`, `StopFailure`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PermissionDenied`, `Notification`, `Elicitation`, `PreCompact`, `SubagentStop`, `InstructionsLoaded`.

Entrada común por stdin (JSON): `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`; `PreToolUse`/`PostToolUse` añaden `tool_name`, `tool_input` (y `tool_response` en Post). `Stop` añade `stop_hook_active`.

Salida: exit 0 + JSON en stdout; exit 2 = bloqueo con stderr como razón. JSON: `hookSpecificOutput.permissionDecision` (`allow|deny|ask`) + `permissionDecisionReason` en PreToolUse; `additionalContext` en SessionStart/UserPromptSubmit/PostToolUse; `decision: "block"` + `reason` en Stop/UserPromptSubmit/PostToolUse; `systemMessage`; `continue: false` + `stopReason`.

Matchers: nombre exacto, `A|B`, regex (`mcp__gmail__send.*`, `mcp__.*`), `*`. Nombres MCP: `mcp__<server>__<tool>`. Tipos de handler: `command`, `http`, `mcp_tool`, `prompt` (con `model`), `agent`. Campos: `timeout`, `once`, `statusMessage`, `if`. Los plugins llevan hooks en `hooks/hooks.json` y disponen de `${CLAUDE_PLUGIN_ROOT}`.

## 4. Plugins

Estructura verificada con `claude plugin init --with skills agents hooks mcp output-style` **[CLI]**: `.claude-plugin/plugin.json`, `skills/<n>/SKILL.md`, `agents/*.md`, `hooks/hooks.json`, `.mcp.json`, `output-styles/*.md` (frontmatter `force-for-plugin: true`, `keep-coding-instructions`). Instalación: `claude plugin install <git|ruta|plugin@marketplace>`, `--plugin-dir` para desarrollo, `claude plugin validate --strict` para CI.

## 5. Skills

Frontmatter: `name`, `description`, `disable-model-invocation`, `user-invocable`, `allowed-tools`, `context: fork`, `agent`, `model`, `memory`, `arguments`. Variables: `$ARGUMENTS`, `$0..`, `${CLAUDE_SESSION_ID}`, `${CLAUDE_PROJECT_DIR}`, `${CLAUDE_SKILL_DIR}`. Inyección dinámica con `` !`comando` ``. Ubicaciones: `~/.claude/skills`, `.claude/skills`, `<plugin>/skills`. Las skills de usuario no se excluyen por proyecto (solo con `skillOverrides` o `--disable-slash-commands`); por eso el harness recomienda que las skills del usuario se copien al workspace.

## 6. Agentes

Frontmatter: `name`, `description`, `tools`, `disallowedTools`, `model`, `permissionMode`, `maxTurns`, `hooks`, `memory`, `skills`, `background`, `isolation`.

## 7. CLAUDE.md y reglas

`./CLAUDE.md`, `./.claude/CLAUDE.md`, `CLAUDE.local.md`, `~/.claude/CLAUDE.md`, `.claude/rules/*.md` con `paths:`. `claudeMdExcludes` para excluir por glob. `--add-dir` no carga CLAUDE.md salvo `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1`.

## 8. Límites y modo auto

`--max-turns`, `--max-budget-usd` (solo `-p`), `maxTurns` en agentes, `autoMode.hard_deny/soft_deny/allow`, `sandbox.enabled` (macOS Seatbelt, Linux bwrap), `permissions.disableBypassPermissionsMode`. Agent SDK: `maxTurns`, `permissionMode`, `canUseTool`, `hooks`.

## 9. Personalización

`--append-system-prompt[-file]`, `appendSystemPrompt` en settings, `--system-prompt` (reemplazo total), output styles en plugin (`force-for-plugin`), CLAUDE.md. `--system-prompt-snapshot` **[CLI]** fija el prompt por conversación.

## Puntos no confirmados por la documentación fetcheada

- Semántica exacta de `--bare` con hooks de plugin (la ayuda dice "skip hooks"): el harness no usa `--bare`.
- Si `enabledPlugins: {}` en proyecto desactiva realmente plugins instalados a nivel usuario: se usa como segunda capa; la primera es `--setting-sources project,local`.
