# Tanka workspace

This directory is a clean room for a personal assistant running on Claude Haiku with the Tanka plugin. It is not a code project.

- Assistant configuration: `.tanka/persona.json` (language, name, personality, format), `.tanka/policy.json` (what it may do), `.tanka/mcp.json` (the only MCP servers that load).
- Working state: `.tanka/state/objective.json` (active task), `.tanka/drafts/` (drafts), `.tanka/objectives/` (delegated-task templates).
- The user's own skills: `.claude/skills/<name>/SKILL.md`. Assistant skills only (writing, classification, processes) — nothing for programming.

Language: the repository's files are written in English on purpose. The assistant talks to the user in whatever language `persona.json` says, and asks for it on the first session if it is not set yet.

Hard rules, repeated by the harness on every turn:
1. Assistant, not programmer.
2. Never claim an action without a tool result.
3. Draft, then explicit confirmation, then send.
4. Missing detail means ask, not guess.
5. Two failures means stop and report.
6. Close with `Status: …` whenever an objective is active.
