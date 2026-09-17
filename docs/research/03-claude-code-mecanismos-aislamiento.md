# Claude Code mechanisms for a clean environment, guardrails and customization

Primary source: official documentation at https://code.claude.com/docs/en/ (settings, settings-reference, model-config, hooks, hooks-guide, plugins, plugins-reference, skills, sub-agents, permission-modes, auto-mode-config, sandboxing, memory, cli-reference, headless, agent-sdk). Additionally verified against `claude --help` from CLI 2.1.273 on 2026-09-16 (marked as **[CLI]**).

## 1. Settings scopes and isolation

Precedence (highest to lowest): managed → `--settings` (CLI) → `.claude/settings.local.json` → `.claude/settings.json` → `~/.claude/settings.json`. Arrays and objects are **merged** across layers, so a project does not "erase" what the user has at the global level: it can only override scalar keys or deny explicitly.

What does guarantee real isolation is the CLI:

| Flag **[CLI]** | Effect |
|---|---|
| `--setting-sources project,local` | Ignores `~/.claude/settings.json` (hooks, permissions, plugins enabled at the user level). |
| `--strict-mcp-config --mcp-config <archivo>` | "Only use MCP servers from --mcp-config, ignoring all other MCP configurations". It is the only way to exclude the MCP servers in `~/.claude.json` and those of third-party plugins. |
| `--plugin-dir <ruta>` | Loads a plugin "for this session only" without installing it. |
| `--restricted` | Removes Bash/PowerShell/REPL and WebFetch unless `--tools` names them, ignores user/project/local settings (managed and `--settings` do apply), confines the file tools to the working dir, rejects `bypassPermissions`. |
| `--safe-mode` | Disables ALL customizations (including our plugin); it is for diagnosis, not for operating. |
| `--bare` | Skips hooks, plugin sync, auto-memory, CLAUDE.md autodiscovery; preserves `--plugin-dir`, `--settings`, `--mcp-config`. Useful for headless mode, but "skip hooks" makes it incompatible with hook-based guardrails. |
| `--disallowedTools`, `--allowedTools`, `--tools` | Per-session trimming of built-in tools. |
| `--model haiku`, `--effort`, `--max-turns`, `--max-budget-usd` (only with `-p`), `--permission-mode`, `--permission-prompts none` (only with `-p`) | Model, limits and permissions per session. |

Relevant settings keys: `permissions.allow/deny/ask/defaultMode`, `permissions.disableBypassPermissionsMode`, `enabledPlugins` (`{}` or `{"x": false}` at the project level), `disabledMcpjsonServers`, `enabledMcpjsonServers`, `deniedMcpServers`, `claudeMdExcludes` (can exclude `~/.claude/CLAUDE.md` by glob; not the managed one), `disableBundledSkills`, `skillOverrides`, `hooks`, `model`, `effortLevel`, `sandbox.*`, `autoMode.*`.

Conclusion: the "clean room" is achieved with a **launcher** combining `--setting-sources project,local` + `--strict-mcp-config --mcp-config` + `--plugin-dir` + `--disallowedTools`, plus a project `.claude/settings.json` with `enabledPlugins: {}` and `claudeMdExcludes` as a second layer.

## 2. Model

- `model: "haiku"` in settings, `--model haiku`, `/model haiku`, `ANTHROPIC_MODEL`, `ANTHROPIC_DEFAULT_HAIKU_MODEL` (what the alias resolves to), `CLAUDE_CODE_SUBAGENT_MODEL`.
- Subagents: `model: haiku|sonnet|opus|inherit` in frontmatter.
- `effortLevel` / `--effort` exists in Claude Code **[CLI]**; according to the API, Haiku 4.5 does not support `effort` (see report 01), so it must not be relied upon.

## 3. Hooks

Events: `SessionStart`, `SessionEnd`, `Setup`, `UserPromptSubmit`, `Stop`, `StopFailure`, `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`, `PermissionDenied`, `Notification`, `Elicitation`, `PreCompact`, `SubagentStop`, `InstructionsLoaded`.

Common input via stdin (JSON): `session_id`, `transcript_path`, `cwd`, `permission_mode`, `hook_event_name`; `PreToolUse`/`PostToolUse` add `tool_name`, `tool_input` (and `tool_response` in Post). `Stop` adds `stop_hook_active`.

Output: exit 0 + JSON on stdout; exit 2 = block, with stderr as the reason. JSON: `hookSpecificOutput.permissionDecision` (`allow|deny|ask`) + `permissionDecisionReason` in PreToolUse; `additionalContext` in SessionStart/UserPromptSubmit/PostToolUse; `decision: "block"` + `reason` in Stop/UserPromptSubmit/PostToolUse; `systemMessage`; `continue: false` + `stopReason`.

Matchers: exact name, `A|B`, regex (`mcp__gmail__send.*`, `mcp__.*`), `*`. MCP names: `mcp__<server>__<tool>`. Handler types: `command`, `http`, `mcp_tool`, `prompt` (with `model`), `agent`. Fields: `timeout`, `once`, `statusMessage`, `if`. Plugins carry their hooks in `hooks/hooks.json` and have `${CLAUDE_PLUGIN_ROOT}` available.

## 4. Plugins

Structure verified with `claude plugin init --with skills agents hooks mcp output-style` **[CLI]**: `.claude-plugin/plugin.json`, `skills/<n>/SKILL.md`, `agents/*.md`, `hooks/hooks.json`, `.mcp.json`, `output-styles/*.md` (frontmatter `force-for-plugin: true`, `keep-coding-instructions`). Installation: `claude plugin install <git|ruta|plugin@marketplace>`, `--plugin-dir` for development, `claude plugin validate --strict` for CI.

## 5. Skills

Frontmatter: `name`, `description`, `disable-model-invocation`, `user-invocable`, `allowed-tools`, `context: fork`, `agent`, `model`, `memory`, `arguments`. Variables: `$ARGUMENTS`, `$0..`, `${CLAUDE_SESSION_ID}`, `${CLAUDE_PROJECT_DIR}`, `${CLAUDE_SKILL_DIR}`. Dynamic injection with `` !`comando` ``. Locations: `~/.claude/skills`, `.claude/skills`, `<plugin>/skills`. User skills cannot be excluded per project (only with `skillOverrides` or `--disable-slash-commands`); that is why the harness recommends copying the user's skills into the workspace.

## 6. Agents

Frontmatter: `name`, `description`, `tools`, `disallowedTools`, `model`, `permissionMode`, `maxTurns`, `hooks`, `memory`, `skills`, `background`, `isolation`.

## 7. CLAUDE.md and rules

`./CLAUDE.md`, `./.claude/CLAUDE.md`, `CLAUDE.local.md`, `~/.claude/CLAUDE.md`, `.claude/rules/*.md` with `paths:`. `claudeMdExcludes` to exclude by glob. `--add-dir` does not load CLAUDE.md unless `CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=1`.

## 8. Limits and auto mode

`--max-turns`, `--max-budget-usd` (only with `-p`), `maxTurns` in agents, `autoMode.hard_deny/soft_deny/allow`, `sandbox.enabled` (macOS Seatbelt, Linux bwrap), `permissions.disableBypassPermissionsMode`. Agent SDK: `maxTurns`, `permissionMode`, `canUseTool`, `hooks`.

## 9. Customization

`--append-system-prompt[-file]`, `appendSystemPrompt` in settings, `--system-prompt` (full replacement), output styles in a plugin (`force-for-plugin`), CLAUDE.md. `--system-prompt-snapshot` **[CLI]** pins the prompt per conversation.

## Points not confirmed by the fetched documentation

- Exact semantics of `--bare` with plugin hooks (the help says "skip hooks"): the harness does not use `--bare`.
- Whether `enabledPlugins: {}` at the project level actually disables plugins installed at the user level: it is used as a second layer; the first is `--setting-sources project,local`.
