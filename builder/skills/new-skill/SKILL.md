---
name: new-skill
description: Create or reshape a Tanka skill — the short routing guide the Haiku assistant reads to pick its tools. Use when the user wants Tanka to handle a new domain (a new account, service or process), says "add a skill", "crea una skill para Tanka", or when a skill is too long, has shell commands, or fails `tanka tools check`.
argument-hint: "<skill name> <domain>"
---

# New Tanka skill

A Tanka skill is a lookup, not a manual: which tool answers which request, what to ask first, and what is out of scope. The assistant runs on **Claude Haiku** and cannot run code, so the skill only talks about its tools and about questions to the user.

Read `docs/skill-rules.md` and `docs/tool-rules.md` before writing. The workspace path is in the system prompt and in `$TANKA_WORKSPACE`.

## 1. Scope it with the user

- One domain the user recognises by name. If they describe two, make two skills.
- List the requests they will make, in their words. Group them: each group becomes one tool or one recipe.
- List the requests that sound related but will **not** be covered. They become the out-of-scope section.
- Run `bin/tanka tools check "$TANKA_WORKSPACE"` and tell the user how much of the 15-tool budget is left. Design within it (at most 6 tools for this skill).

## 2. Name it

Lowercase letters, digits and hyphens. It becomes the prefix of every tool (`win10-vm` → `win10_vm_*`), so choose it once.

## 3. Write `SKILL.md`

`$TANKA_WORKSPACE/.claude/skills/<name>/SKILL.md`, under 100 lines, in the user's language:

1. Frontmatter: `name` equal to the directory, `description` with the domain and the trigger words the user actually says.
2. Scope, in one or two sentences.
3. "Which tool" table: request in the user's words → tool.
4. Recipes: numbered, at most 5 steps, each one tool call or one question. A confirmation step right before every `send` or `modify` tool.
5. Rules: what never to repeat, when to stop.
6. Out of scope: what is not covered and the exact sentence to say (no tool for it yet; it can be added with `tanka dev`).

If there is a long working manual for this domain already (for example in `~/.claude/skills/`), leave it where it is for authors. The workspace skill only routes.

## 4. Build its tools

Use the `new-tool` skill for each tool, one at a time, verifying each before the next.

## 5. Verify the skill

1. `bin/tanka tools check "$TANKA_WORKSPACE"` → every tool `ok`, no warnings for the skill.
2. Each row of the "Which tool" table matches exactly one tool description.
3. `bin/tanka run "<a request from each recipe>" "$TANKA_WORKSPACE" --max-turns 8 --budget 0.30` → the calls in `.tanka/state/sessions/` follow the recipe. (Read-only recipes only, unless the user approves a real send.)
4. One out-of-scope request → the assistant says it has no tool and stops.

Report the skill, its tools, the checks, and what was not run.
