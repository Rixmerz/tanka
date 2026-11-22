# Tanka v2

A sandbox of autonomous Claude Code agents behind a single delegator, built to
run entirely in Claude Code cloud sessions.

- **Tanka** (the session you talk to) only routes. It sees a registry of agents
  (name, one line, category, model, effort) and delegates. It never reads an
  agent's reply: replies reach you as file cards, so its context stays small.
- **Each agent** is a self-contained folder under `agents/<name>/` with its own
  `CLAUDE.md`, rules, skills, MCP servers, permissions, model, effort and memory.
  It runs as a one-shot `claude -p` inside its folder, reads its notes when it
  starts and updates them when it ends.

## Layout

```
docs/delegator.md         the delegator's rules (injected by the mod)
agents/<name>/            one agent (created with create_agent, never by hand)
  agent.json              name, description, category, model, effort
  CLAUDE.md               role and rules; imports memory/notes.md
  .claude/settings.json   permissions
  .claude/rules/          topic rules
  .claude/skills/         skills
  .mcp.json               MCP servers
  memory/notes.md         the agent's own memory, kept in git
  reports/                replies (not in git)
templates/agent/          what create_agent copies
plugin/tanka/             the Claude Code mod that provides the tools
docs/plan.md              the roadmap
```

## Tools (from the mod)

| Tool | What it does |
| --- | --- |
| `delegate(agent, task)` | runs the agent with its model and effort; the reply comes back as a file card |
| `create_agent(...)` | creates an agent from the template and registers it |
| `edit_agent(...)` | changes an agent's description, category, model, effort or instructions |
| `list_agents()` | the registry |
| `archive_agent(name)` | moves an agent to `agents/_archived/` |

## Install

The repository is its own plugin marketplace. A session opened on this repo
picks the plugin up from `.claude/settings.json` (once this branch is merged
to the default branch). By hand:

```
/plugin marketplace add Rixmerz/tanka
/plugin install tanka@tanka
```
