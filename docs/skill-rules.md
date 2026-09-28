# Skill rules

A skill tells the assistant **when** to use which tool and what to do around the calls. Tools do the mechanical work (see [tool-rules.md](tool-rules.md)). Skills carry the judgment: which tool fits which request, what to ask first, when to stop.

The assistant runs on Claude Haiku, reads the skill in the middle of a task, and cannot run code. So a skill is short, is organised as a lookup, and mentions nothing the assistant cannot do with its tools.

Rules marked **[checked]** are enforced by `tanka tools check` (as a failure when they block tools, as a warning otherwise).

## 1. One skill, one domain

- A skill covers one thing the user recognises by name: their library account, Gmail, their calendar, a Windows VM. Not "email and calendar"; not "utilities".
- Directory: `<workspace>/.claude/skills/<skill>/`. **[checked]** The name is lowercase letters, digits and hyphens. It becomes the prefix of every tool in the skill (`win10-vm` → `win10_vm_*`), so pick it once.
- **[checked]** At most 6 tools per skill, and the whole workspace shares a budget of 15. Check how many are left before designing.

## 2. Frontmatter

```yaml
---
name: library
description: Loans, holds and the catalogue of the user's public library account. Use whenever the user mentions the library, a book they borrowed, a due date, renewing, a hold, or looking for a book.
---
```

- **[checked]** `name` is the directory name.
- **[checked]** `description` exists. It is what makes Claude Code offer the skill, so it names the domain in one sentence and then **lists the trigger words** the user actually says, in the user's language.

## 3. Body: five sections, in this order

At most **100 lines** [checked as a warning]. A long skill is read partially, and the part that is skipped is the part that mattered.

1. **Scope** — one or two sentences: whose account, what it covers, and "only what these tools do".
2. **Which tool** — a table, one row per kind of request, request on the left in the user's words, tool on the right. **[checked]** Every tool of the skill appears in the skill.
3. **Recipes** — numbered steps for the multi-call tasks. Each step is one tool call or one question to the user. At most 5 steps. Say where each value comes from ("the book_id from library_search").
4. **Rules** — what to confirm and when, what never to repeat, when to stop.
5. **Out of scope** — the requests people will make that no tool covers, and the exact thing to say: that there is no tool for it yet and it can be added with `tanka dev`. This section is what stops Haiku from improvising.

## 4. Confirmation lives in the skill

Tools are pre-approved: the harness does not ask the user before a tool runs. So every recipe that ends in a `send` or `modify` tool has a step right before it:

> If the user did not state every value themselves, confirm in one line the values that came from a tool or from your own judgment.

and the rules section says the result is visible to others and must not be repeated. A missing value is always a question, never a guess.

This matches what Haiku did in testing (one run each way with a real skill, so treat it as observed, not guaranteed), so write the step this way rather than as an unconditional "always confirm": when the user's own message carries every value, Haiku acts without confirming even if the skill says to; when any value is missing or had to be looked up, it asks. A skill that says "always" is a rule the model visibly skips, and teaches it that skill rules are optional.

## 5. Write for a model that cannot run code

- **No shell commands, code blocks, URLs to visit, or selectors.** [shell code blocks are checked as a warning] The assistant has no Bash and no browser; a recipe it cannot run is a recipe it will try to work around, or hand to the user as "run this yourself".
- Name tools exactly, in backticks: `library_loans`.
- One instruction per line. Imperative. No background history, no "why it works" essays — keep that knowledge in the tool's code or in a separate reference for authors.
- Write in the **user's language**. The tool descriptions use the same language as their skill.

## 6. Keep the author's knowledge out of the assistant's skill

A full working manual (endpoints, traps, recipes for a browser) is valuable, but it is for the author who builds tools, not for the assistant. Keep it in a separate skill outside the workspace (for example `~/.claude/skills/<name>/`) and give the workspace a short skill that only routes to tools.

## 7. Before calling it done

1. `tanka tools check` lists every tool of the skill as `ok`, and no warnings for the skill.
2. Read the "Which tool" table against the tool descriptions: each request row matches exactly one tool, and no tool is missing.
3. `tanka run "<a request the user would make>"` for each recipe, then check `.tanka/state/sessions/` for the calls: they are the recipe's steps, in order.
4. One request from the out-of-scope list: the assistant says it has no tool, and does not try another way.
