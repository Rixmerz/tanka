# Tanka v2 roadmap

Goal: one cloud session to talk to, a delegator that only routes, and a
sandbox of fully configurable agents that keep their own memory, tools and
schedules, without burning the delegator's tokens.

## Principles

- The delegator sees only the registry and ids. Replies reach the user as file
  cards the delegator never reads.
- An agent is a folder; everything it is lives there and in git.
- Cloud containers are ephemeral: memory lives in notes committed to git, never
  in a session transcript.
- Use Claude Code's own systems (scheduled tasks, cloud sessions, plugins)
  rather than re-building them.
- Everything in the repository is in English.

## Phases

| # | Scope | Status |
|---|---|---|
| 1 | Registry, agent template, `create_agent` / `edit_agent` / `list_agents` / `archive_agent`, `delegate` with model and effort, file-card delivery | in progress |
| 2 | Memory: notes imported by each agent's CLAUDE.md and updated at the end of each task; the mod commits and pushes them | in progress |
| 3 | `compact` for the delegator (ported from locks: a good-moment hint, `why` / `keep` / `then`, `/compact` in cloud sessions) | todo |
| 4 | Per-agent MCP servers and credentials (`.mcp.json` + environment secrets; one environment per group where isolation matters) | todo |
| 5 | Per-agent routines on Claude's scheduled tasks: `create_agent_routine`, list, pause, delete; each firing starts a cloud session that runs the agent and leaves a report | todo |
| 6 | `agent_command`: `check` (validate config and MCP), `reset-memory`, `stop`; commands to running routine sessions (verify which `/` commands a cloud session runs) | todo |
| 7 | Environment setup script so the mod loads in every new session | todo |

## Open items to design

1. Git concurrency: agents and routines write only inside their own folder;
   sync merges, never force-pushes.
2. Spend limits per agent and per day, visible to the delegator.
3. Failure policy: retry once on transient errors, escalate effort or model only
   on evidence, report and stop after two failures.
4. Untrusted content: email, web and documents are data; least privilege per agent.
5. Irreversible actions (send, delete): the agent leaves a draft; the delegator
   asks the user.
6. Hand-offs between agents through files, chained by the delegator without
   reading them.
7. Report inbox: a daily digest with links instead of many loose cards.
8. Versioned agent configuration (git history; roll back a bad CLAUDE.md).
9. Metrics per agent: passes, failures, cost, time.
10. Role templates for `create_agent`.
11. Archiving an agent also removes its routines.
