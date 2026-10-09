# Tool rules

A Tanka tool is what the assistant uses to act outside the conversation. Tools are written by a person (or by a stronger model in `tanka dev`) and used by Claude Haiku, so every rule here is about one thing: **a small model has to pick the right tool, fill its fields without guessing, and understand the result on the first try.**

Rules marked **[checked]** are enforced by `plugin/scripts/tanka_tools.py`. A tool that breaks one is not loaded, and `tanka tools check` says why. The numbers come from that file; if this page and the code disagree, the code wins and this page is the bug.

Evidence cited as `research/01` and `research/02` is in [`docs/research/`](research/).

## 1. Where a tool lives

```
<workspace>/.claude/skills/<skill>/
├── SKILL.md                      the skill: when to use which tool
└── tools/
    ├── <skill>_<name>.json       the manifest
    ├── <skill>_<name>.py         the command it runs (optional; any program works)
    └── <skill>_<name>.md         a subagent's prompt, when the manifest has `agent` instead of `run`
```

- **[checked]** A tool belongs to exactly one skill, and its name starts with that skill's prefix: skill `library` → `library_*`, skill `win10-vm` → `win10_vm_*`. The prefix comes from the directory, not from the author.
- **[checked]** The manifest file is named after the tool.
- **[checked]** The skill's `SKILL.md` mentions every tool it ships. A tool nothing points to is a tool Haiku will not pick.
- `tools/` holds manifests and their scripts only. Data files go elsewhere, because every `*.json` in it is read as a manifest.

## 2. Decide whether it should be a tool at all

Make a tool when **one user intent maps to one mechanical action** with a result you can check: "what do I have on loan", "renew this book".

Do not make a tool for:

- **Judgment.** Which book to renew first, how to phrase a reply, whether a message is urgent. That goes in the skill's text; the tool does the mechanical part after the decision.
- **Anything destructive.** **[checked]** `destructive` is not a valid effect. Deleting, emptying or revoking stays with the user. This is the invariant Tanka is built on.
- **A general-purpose escape hatch.** No `run_command`, `browse`, `http_request`, `sql`. A tool that can do anything gives Haiku back every way to get lost, and gives a prompt injection in an email somewhere to go (research/01: ~7% of injections succeed against Haiku without a human step).
- **Something done once.** Do it by hand.

## 3. Budget: fewer tools, better choices

| Limit | Value | Why |
| --- | --- | --- |
| Tools in the whole workspace | **15** [checked] (40 with the standard guardrails) | Haiku's tool-selection accuracy drops below 90% between 10 and 15 tools (research/02) |
| Tools per skill | **6** [checked] | a skill with more is two skills |
| Params per tool | **6** [checked] | each field is a chance to invent a value |
| Required params | **4** [checked] | Haiku infers missing values instead of asking (research/01) |
| Examples per tool | **1-3** [checked] | examples raise complex-parameter accuracy from 72% to 90% (research/01) |

Above 15 the extra tools are not loaded (alphabetical cut). Above 6 in one skill, none of that skill's tools load. Plan the budget before adding: if the workspace is at 13, the next skill gets 2.

## 4. Name

- **[checked]** `lowercase_snake_case`, at most 48 characters, and the part after the prefix at least 3 characters.
- Reads are named by what they return: `library_loans`, `library_search`.
- Actions are named by verb and object: `library_renew_loan`, `library_place_hold`.
- Use the words the user uses. If they say "renew", the tool is not `extend_checkout`.
- Two tools in the same skill never differ by one word that means the same thing (`get_x` next to `fetch_x`).

## 5. Effect class

**[checked]** Every tool declares `effect`: one of `read`, `draft`, `modify`, `send`.

| Effect | Means | Example |
| --- | --- | --- |
| `read` | changes nothing anywhere | list loans, find a file |
| `draft` | creates something only the user sees | save an email draft |
| `modify` | changes state other people may notice, reversibly | label, archive, move |
| `send` | other people see it, and it cannot be taken back | place a hold, send an email |

Tools run **without asking the user** (they are pre-approved). The class still matters: it limits which tools an active objective allows, and it lets the harness block the assistant from claiming "I published it" without a successful `send` result. Pick the strongest class that applies. One tool, one class: never a parameter that switches a tool between reading and writing.

Because `send` tools do not ask, the **skill** must put a confirmation step before them (see [skill-rules.md](skill-rules.md)).

## 6. Description

The description is the only thing Haiku reads when choosing. **[checked]** 150-900 characters and at least 3 sentences. Anthropic's guidance is "extremely detailed, at least 3-4 sentences" (research/01).

Write exactly these parts, in this order, in the language of the skill:

1. **What it does**, with the object and where it comes from. "Lists the books the user has on loan from the public library, with…"
2. **When to use it**, as the user would ask. "Use it when the user asks what they have borrowed or when something is due, and before library_renew_loan…"
3. **When not to, and what to use instead.** Name the sibling tool. "It does not search the catalogue (that is library_search)."
4. **What it returns and what to do with it.** "One line per loan with its due date; if several match, ask which one."

Add, when true:

- the **prerequisite**: "needs the loan_id: call library_loans first";
- the **consequence**: "the library records it immediately"; "calling it twice duplicates it, so never repeat a call that succeeded";
- the **missing-data rule**: "if any of these is missing, ask; do not assume it".

Do not write marketing ("powerful", "seamless"), implementation details (URLs, HTTP, selectors), or instructions aimed at a different tool.

**[checked]** The examples are appended to the description as `Example: {...}` lines (MCP has no `input_examples` field), and the total must stay under 1800 characters, because Claude Code truncates tool descriptions at 2 KB.

## 7. Params

**[checked]** Types are `string`, `integer`, `number`, `boolean`. No arrays, no objects, no nesting. A list the model has to build is a list it will build wrong: make one call per item, or let the command compute the list.

**[checked]** Allowed keys per param: `type`, `description`, `required`, `enum`, `default`, `pattern`, `minimum`, `maximum`. Unknown arguments are rejected at call time.

- **Enum whenever the set of values is known** (2-12 values, [checked]). An enum cannot be misspelled.
- **Ids are integers** when the system's ids are numbers. `loan_id: 48213`, not a URL.
- **Bounds on numbers** (`minimum`/`maximum`) and **`pattern` on strings with a format** (paths, codes, emails). A path param always has a pattern that pins it under the one directory it may touch.
- **Defaults for optional params**, so the command always gets a value. **[checked]** A required param has no default.
- **Nothing the command can work out by itself.** If the book title follows from the id, do not ask for both.
- **Param description formula** (10-250 characters, [checked]): what it is + its format + one example + where to get it. "Numeric loan id, as library_loans shows it (e.g. 48213)."
- Param names are `snake_case`, at most 24 characters, in the user's words.

## 8. Examples

**[checked]** 1-3 complete argument objects, each one valid against the params. Use real values. If the tool has optional params, one example uses them and another does not.

## 9. The command

A tool has either `run` (a command, below) or `agent` (a subagent: a stronger model does the work; see [subagent-rules.md](subagent-rules.md)), never both.

```json
"run": ["python3", "library_loans.py"],
"run": ["libcli", "renew", "--loan={loan_id}", ["--note", "{note}"]]
```

- **[checked]** `run` is an argv list: no shell, no pipes, no `$VAR`. `run[0]` is a fixed program.
- **[checked]** `{param}` placeholders name declared params. An optional param only appears inside a **group** (a nested list), and the whole group is dropped when the value is absent.
- **[checked]** A value that fills a whole argument cannot start with `-`, so nobody can smuggle a flag in. Put fixed flags before it (`--param`, `x={x}`).
- **[checked]** `timeout_sec` between 1 and 600 (default 60). Set it to the real worst case plus margin.
- The command also receives every argument, defaults applied, as **JSON on stdin**. Scripts should read that instead of parsing argv.
- It runs with the working directory set to `tools/`, and with `TANKA_WORKSPACE`, `TANKA_SKILL` and `TANKA_TOOL` in the environment.
- Prefer Python with the standard library, or an existing CLI. A script is one file named after its tool.

## 10. Output

The output is the next thing Haiku reads, and it decides the next call from it.

- **[checked]** Anything past 6000 characters is cut, with a note telling the model to narrow the request. Design so that never happens: filters, limits, the most relevant first.
- **First line is a summary with a count**: "3 loan(s), the next one due on 2026-10-04:".
- **One line per item**, the same fields in the same order. No raw JSON dumps of whole API objects.
- **Include the ids the next tool needs** (`loan_id`, full paths), so the model copies instead of inventing.
- Nothing on stdout that is not for the model (progress bars, debug).

## 11. Errors

- Exit non-zero and print **one sentence that says what to do next**: "No loan with loan_id 123. Use library_loans to see the valid ids." Tanka returns it to the model as an error with the last 1500 characters of stderr.
- Bad arguments never reach the command: Tanka rejects them with a message that names the field and the fix.
- The harness stops a tool after 3 consecutive failures and forbids the same call more than twice per turn. An error that says "retry" invites the loop Haiku is known for (research/02); say "tell the user" instead when retrying cannot help.

## 12. Safety for `modify` and `send`

- Idempotent when you can (publishing the same file twice should not create two copies). When you cannot, the description says so and says not to repeat a successful call.
- Scope everything: one account, one directory, one host. The command enforces it; the pattern on the param repeats it.
- Never put secrets in the manifest or the script. Use the credential store of the program you call.

## 13. Before calling it done

1. `tanka tools check` shows the tool as `ok`, with no problems.
2. `tanka tools test <tool> '<example>'` once per example: the output follows section 10.
3. One call with a bad argument: the error says what to fix.
4. A real session: `tanka run "<a request as the user would say it>"`, then check in `.tanka/state/sessions/` that the calls were the ones the skill's recipe describes.
5. `send` and `modify` tools: never test against real people or real accounts without the user's explicit OK. Test the read tools and the argument validation, and say what was not tested.
