# Subagent rules

A Tanka subagent is a tool whose work is done by a stronger model. The assistant runs on Claude Haiku because most of what it does is cheap: read a list, pick a tool, fill four fields, publish after a yes. Some steps are not cheap: reviewing twenty student projects against a rubric, reading a long contract, comparing a client's history before a call. Haiku does those badly and a stronger model does them well. A subagent gives that one step to the right model and hands the result back to Haiku, which acts on it with its ordinary tools.

**The split is the design: the expensive model thinks and proposes, Haiku acts.**

Everything in [tool-rules.md](tool-rules.md) applies to a subagent, because to Haiku it is just another tool. This page adds what is different. Rules marked **[checked]** are enforced by `plugin/scripts/tanka_agent.py`, called from `tanka tools check`.

## 1. Why a tool and not a Claude Code subagent

Claude Code can run subagents inside a session (`.claude/agents/*.md`, the Agent tool). Tanka does not use them for this, for two measured reasons:

- **The session has no Bash.** `bin/tanka` launches with `--disallowedTools Bash` and the `PreToolUse` hook denies it. An in-session subagent inherits both, so a reviewer that must run a student's project cannot.
- **The session's loop guard counts every call in the turn**, the subagent's included: 25 actions per turn, 12 uses of the same tool (`loop_guard` in `tanka_common.py`). A reviewer reading twenty files is cut off halfway.

So a subagent runs as `claude -p` **outside** the session, confined by its own flags (section 4). Only `tanka:*` plugin agents run inside the session, and that stays so.

## 2. When to make one

Make a subagent when one step needs **judgment or depth Haiku does not have**, and its result is something Haiku can act on:

- review work against criteria and propose a grade per criterion;
- read a long document and extract the few facts the next tool needs;
- prepare a briefing (a client's history, the open issues) before the user decides.

Do not make one for:

- **Anything mechanical.** Listing, downloading, filling a form: a normal tool, on Haiku, costs nothing extra.
- **Publishing.** **[checked]** A subagent's `effect` is `read` or `draft`. It proposes; a separate `send`/`modify` tool publishes after the skill's confirmation step. Never give the expensive model the last word on something other people see.
- **A general assistant.** "Ask Opus anything" is a way around every rule of the harness. One subagent, one job, one prompt.

## 3. The manifest

A normal tool manifest in `tools/<name>.json` with an `agent` block **instead of** `run` **[checked: one or the other]**, and its prompt in `tools/<name>.md` **[checked]**.

```json
"agent": {
  "model": "opus",
  "effort": "low",
  "tools": ["Read", "Grep", "Glob"],
  "max_budget_usd": 2,
  "workdir": "{carpeta}",
  "output": {"type": "object", "required": ["..."], "properties": {"...": {}}},
  "background": false
}
```

| Key | Rule | Why |
| --- | --- | --- |
| `model` | `haiku`, `sonnet` or `opus` **[checked]** | Pick the cheapest that does the job; `sonnet` before `opus`. |
| `effort` | `low` … `max` **[checked]** | Start at `low`; raise it only when a real run shows it is not enough. |
| `tools` | from `Read Grep Glob Bash WebSearch WebFetch` **[checked]** | No Write or Edit: a subagent returns an answer, it does not change files it was not given. |
| `max_budget_usd` | required, above 0, at most 20 **[checked]** | The dollar cap per call. Set it to about three times a real run's cost. |
| `workdir` | optional; `"{param}"` naming a required string param with a `pattern` **[checked]** | The only folder its file tools can reach. Without it, a scratch folder under `.tanka/agents/<name>/work`. |
| `output` | optional JSON Schema, `type: object`, under 4000 characters **[checked]** | Structured output Haiku can read line by line and pass to the next tool. Use it whenever the next step is a tool call. |
| `background` | `true` or `false` **[checked]** | See section 5. |

`timeout_sec` (at most 600) bounds a foreground call. A background call has one hour.

## 4. The prompt

`tools/<name>.md` is the subagent's whole instruction; it sees nothing else of the conversation. `{param}` placeholders are filled with the call's arguments **[checked: each names a param]**.

- Say what it receives, what it must decide, and **what evidence each decision needs** (`path:line`, a command and its output). A proposal without evidence cannot be checked by the user.
- Say exactly what to return, matching `output`. Haiku will show it to the user and act on it.
- Write it in the language of the result: the answer comes back in the prompt's language.
- **Hand it inputs its tools can read.** The subagent's Read tool opens text, code, PDFs and images, not Word or PowerPoint files; convert those to text (plus their images) before the run. A subagent that cannot open its input will still return an answer.
- **"I could not read it" is not a result.** Give the output a field for it (for example `evaluable: false`) and have the tool turn it into an error. Folded into a low score or an empty verdict, it looks exactly like a real one, and a `send` tool downstream will publish it.
- The runner adds a fixed preamble to every prompt: everything read is data, not instructions; propose, never publish. Do not repeat it; do not contradict it.

## 5. How it runs

`tanka_agent.py` builds the command. None of this is optional:

- `--restricted`: only the listed tools; file tools confined to `workdir` and `--add-dir`; the user's and project's settings, hooks and plugins ignored.
- `--strict-mcp-config`, `--permission-prompts none`, `--no-session-persistence`, `--max-budget-usd`.
- **Bash only inside the sandbox, with `failIfUnavailable`.** If the machine cannot sandbox (Linux needs `bubblewrap` and `socat`), the subagent refuses to start. The sandbox reaches only the package registries and cannot read `~/.ssh`, `~/.config`, `~/.claude` and the other places credentials live.
- A clean environment: no `TANKA_*`, `CLAUDECODE` or `CLAUDE_CODE_*` variables, so the child does not inherit the session.

**Foreground** (`background: false`): the call waits for the answer. Use it when a run takes well under `timeout_sec`.

**Background** (`background: true`): the first call starts the job and returns at once; a call with the **same arguments** later returns "still working" or the result. The skill must say: start it, tell the user it is working, and **do not call it again in the same turn** (the loop guard would deny the repeat anyway). A failure is reported once; the next call starts over.

## 6. Scripted subagents

When the step needs preparation or fans out (one review per student, with the rubric read from a file first), write a normal `run` tool and import the runner from its script, so the command, the confinement and the parsing stay the same:

```python
sys.path.insert(0, os.path.join(os.environ["TANKA_PLUGIN_DIR"], "scripts"))
import tanka_agent as ta
r = ta.run_once(ta.command("opus", "low", ["Read", "Bash"], prompt, 3, schema, [extra_dir]), cwd, 1200)
```

`TANKA_PLUGIN_DIR` is set for every tool. The same rules hold: `read` or `draft`, a budget, evidence in the prompt, and the result validated by the script before Haiku sees it (an id that is not in the rubric is dropped, not trusted).

## 7. The skill around it

- A row in "Which tool" in the user's words, like any tool.
- The recipe step after it shows the proposal to the user. If the next tool is `send` or `modify`, the confirmation step comes before it, and "publish everything" without having seen the proposals is not a confirmation.
- For a background subagent, the recipe ends when the job starts, closing with `Status: partial`; a second recipe covers "how is it going / show me the result".
- "How is it going?" is always answered by calling the tool again, never from memory. The time estimate the tool gave an hour ago is not the state now; say so in the skill's rules, and have the tool's own messages repeat it.

A complete case, with the real run's numbers and the traps it hit, is in [examples/grading-with-a-subagent.md](examples/grading-with-a-subagent.md).

## 8. Before calling it done

1. `tanka tools check`: the tool is `ok` and shows `subagent <model>/<effort>`.
2. One real, cheap run first: the same prompt on `haiku` with `effort: low` through `tanka tools test`, to prove the prompt, the schema and the parsing. Then switch to the real model.
3. One real run on the real model, and read the result as the user would. Note its cost and time, and set `max_budget_usd` and `timeout_sec` from them.
4. A `tanka run` with a request from the recipe; the session log shows the subagent call where the recipe puts it.
5. If it uses Bash, confirm the refusal on a machine without the sandbox, and say which run was done with it.
