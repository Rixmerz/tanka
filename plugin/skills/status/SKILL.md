---
name: status
description: Show the state of the Tanka workspace — profile, enabled MCP servers, policy, active objective, unset fields. Use when the user asks "how are you set up", "what can you do", "status", "what objective do you have".
allowed-tools: Read, Glob
---

# Workspace state

Read these files with the Read tool, whichever exist. Do not use Bash.

1. `.tanka/persona.json` — who you are and who you work for; note any field that is an empty string, those are still unset.
2. `.tanka/mcp.json` — enabled MCP servers (the `mcpServers` key).
3. `.tanka/policy.json` — per-class decisions (`decisions`) and loop limits (`loop_guard`).
4. `.tanka/state/objective.json` — the active objective, may not exist.
5. `.tanka/objectives/*.json` (with Glob) — available delegated-task templates.

Summarise in plain language, in the user's language, in this order:

- **Who I am**: name, who I work for, language and tone.
- **What I can reach**: enabled MCP servers. If there are more than three, mention that a small model picks tools better with fewer.
- **What's allowed**: reading and drafting freely; modifications and sends need confirmation; nothing destructive; no code.
- **Active objective**: title, goal, done-criteria and status — or "none", plus the templates available.
- **Still unset**: the empty profile fields, and that they can be filled whenever, or all at once with `/tanka:setup`.
- **Limits**: identical calls per turn, actions per turn, consecutive failures.

Do not offer to change the policy from the conversation: that is edited in `.tanka/policy.json` outside the session.
