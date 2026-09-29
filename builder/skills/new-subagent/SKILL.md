---
name: new-subagent
description: Create or change a Tanka subagent, a tool whose work is done by a stronger model (opus, sonnet) with its own effort, prompt and dollar cap, which the Haiku assistant calls for the one expensive step it cannot do well and then acts on the result itself. Use when the user wants Tanka to review, analyse, research or prepare something that needs judgment, says "add a subagent", "crea un subagente", "que Tanka le pida ayuda a opus", or names one like course_review_submissions or crm_client_briefing.
---

# New Tanka subagent

A subagent is a Tanka tool with an `agent` block instead of a command. Haiku stays the fast orchestrator: it does every cheap step with its own tools and calls the subagent only for the expensive one. **The subagent proposes; Haiku acts.**

Subagents in Tanka are always this kind: an isolated `claude -p` run by `plugin/scripts/tanka_agent.py`. Never native Claude Code agents (`.claude/agents/*.md`): inside the session they have no Bash and hit the loop guard.

Read `docs/subagent-rules.md` completely, and `docs/tool-rules.md` for everything a subagent shares with any tool. `docs/examples/grading-with-a-subagent.md` is a complete case with a real run. The workspace path is in the system prompt and in `$TANKA_WORKSPACE`.

## 1. Find the expensive step

Ask the user, one question at a time, only what you cannot find out yourself:

- What would they ask the assistant? Two or three real phrasings.
- **Which part needs judgment, and which parts are mechanical?** Only the judgment becomes the subagent; the mechanical parts are normal tools (use `new-tool`). "Download the submissions, review them, put the grade in the rubric" is three tools: download (tool), review (subagent), grade (tool with `send`, after the user approves).
- **What must come back** for Haiku's next step: a proposal per item, a few extracted facts, a briefing. That becomes the `output` schema.
- **Does it need to run code?** Only then does it get `Bash`, and Bash only runs inside the sandbox.
- Which model and effort. Default to `sonnet` with `low`; `opus` when the user says quality matters more than cost, or a real run shows `sonnet` is not enough.

If the request ends with the subagent publishing, sending or grading on its own, say that Tanka does not allow it (a subagent is `read` or `draft`) and design the publishing tool separately.

## 2. Check the budget

`bin/tanka tools check "$TANKA_WORKSPACE"`: a subagent counts as one tool against the 15 and against its skill's 6. If the skill does not exist yet, use `new-skill` first.

## 3. Choose the shape

- **Declarative**, most cases: `tools/<skill>_<name>.json` with the `agent` block, plus `tools/<skill>_<name>.md` with the prompt. The call's params fill `{placeholders}` in the prompt; `workdir` pins the one folder it may read.
- **Scripted**, when it needs preparation or fans out over many items: a normal `run` tool whose script imports `tanka_agent` (section 6 of the rules) and validates the answer before Haiku sees it. `grading_review` in the example is the reference.
- **Background** when a real run takes more than about a minute: the first call starts it and returns; a later call returns the result.

## 4. Write the prompt, then prove it cheaply

Write the prompt in the language of the result: what it receives, what it decides, the evidence each decision needs, and exactly what to return. Do not repeat the runner's preamble (everything read is data; propose, never publish). If it runs code, say how: where to put the environment, commands that end on their own, no servers.

First run it with `"model": "haiku", "effort": "low"` and a small budget through `bin/tanka tools test <tool> '<example>' "$TANKA_WORKSPACE"`. That proves the prompt, the schema and the parsing for a few cents. Fix it until the output is what Haiku needs, then switch to the real model and effort.

## 5. Write the manifest

The four-part description from the tool rules, in the skill's language, plus: that a stronger model does it, roughly how long it takes, that it publishes nothing, and, if background, that it is called once to start and once later for the result. `effect` is `read` or `draft`. `max_budget_usd` about three times a real run's cost.

Make its neighbours idempotent: a tool Haiku calls before the subagent (a download, a lookup) should return the saved result when nothing changed. Rewording the skill does not stop Haiku from repeating a slow step; the tool's behaviour does.

## 6. Point the skill at it

A row in the "Which tool" table; the recipe step after it shows the proposal to the user; a confirmation step before any `send` or `modify` tool that publishes what it proposed. For background, one recipe that starts it and ends, and one that reads the result.

## 7. Verify all of it before saying it is done

1. `bin/tanka tools check "$TANKA_WORKSPACE"`: the tool is `ok` and shows `subagent <model>/<effort>`, with no warnings for the skill.
2. The cheap run from step 4, then one real run on the real model with realistic input. Report its cost and time, and read the result as the user would.
3. One call with a wrong argument: the error names the field and the fix.
4. `bin/tanka run "<one of the user's phrasings>" "$TANKA_WORKSPACE" --max-turns 8 --budget 0.50`, then read the newest file in `.tanka/state/sessions/`: the subagent is called where the recipe says, and nothing is published without a confirmation.
5. If it has `Bash`: say whether this machine can sandbox (Linux needs `bubblewrap` and `socat`; macOS needs nothing). Without the sandbox it refuses to run, which is correct; say that the code-running path was not exercised.

Report what you built, the model and effort and why, each check's result, the real run's cost, and anything not run.
