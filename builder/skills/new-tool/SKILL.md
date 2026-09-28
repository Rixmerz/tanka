---
name: new-tool
description: Create or change a tool of the Tanka MCP server for an existing skill — manifest, command, checks and a real run on Haiku. Use when the user wants the Tanka assistant to be able to do something new, says "add a tool", "crea una tool", "que Tanka pueda…", or when a tool fails `tanka tools check`.
argument-hint: "<skill> <what the tool should do>"
---

# New Tanka tool

You are building a tool that **Claude Haiku** will use without asking the user first. You are the careful one: the assistant cannot write or fix tools.

Read `docs/tool-rules.md` completely before writing anything. It is short and every rule in it exists because Haiku failed without it. The workspace path is in the system prompt and in `$TANKA_WORKSPACE`.

## 1. Find out what the tool is for

Ask the user, one question at a time, only what you cannot find out yourself:

- What would they say to the assistant? Get two or three real phrasings. These become the "when to use it" sentence and the test prompts.
- What should come back, and what will the assistant do next with it?
- Does it change anything other people can see? That decides the effect (`read`, `draft`, `modify`, `send`). Destructive actions are never tools: say so and stop there if that is what they want.

Then look for the mechanism yourself before asking: an existing CLI, a saved browser routine, a local file, an API the user already has credentials for. Prefer what already works on this machine.

## 2. Check the budget

Run `bin/tanka tools check "$TANKA_WORKSPACE"`. The last line says how many of the 15 tools are used. If the skill already has 6, or the workspace has 15, stop and discuss with the user which tool to merge or drop. Never go over: the extra tools do not load.

If the skill does not exist yet, use the `new-skill` skill first.

## 3. Write the command, then prove it works alone

Put the script next to the manifest: `.claude/skills/<skill>/tools/<skill>_<name>.py`. Python with the standard library, reading its arguments as JSON from stdin. Follow the output and error sections of the rules: a first line with a count, one line per item, the ids the next tool needs, errors that say what to do next.

Run it by hand with realistic input until the output is right. Only then write the manifest.

## 4. Write the manifest

`.claude/skills/<skill>/tools/<skill>_<name>.json`, keys `name`, `effect`, `description`, `params`, `examples`, `run`, `timeout_sec`. The description has the four parts from the rules in the skill's language: what, when, when not (naming the sibling tool), what it returns. Enums for any closed set of values, integers for numeric ids, a `pattern` on every path.

## 5. Point the skill at it

Add the tool to the skill's "Which tool" table, to any recipe that needs it, and, if it is `send` or `modify`, a confirmation step right before it. The check fails until the skill mentions the tool.

## 6. Verify — all of it, before saying it is done

1. `bin/tanka tools check "$TANKA_WORKSPACE"` → the tool is `ok`, no problems, no warnings for the skill.
2. `bin/tanka tools test <tool> '<each example>' "$TANKA_WORKSPACE"` → output as the rules describe.
3. One call with a wrong argument → the error names the field and the fix.
4. `bin/tanka run "<one of the user's phrasings>" "$TANKA_WORKSPACE" --max-turns 8 --budget 0.30`, then read the newest file in `.tanka/state/sessions/` and confirm the calls match the skill's recipe.
5. For `send` and `modify` tools, do **not** run step 2 or 4 against real people, real students or real accounts without the user's explicit OK. Verify the argument validation instead, and say plainly what was not run.

Report what you built, what each check returned, and anything left untested. The assistant picks the tool up in its next session.
