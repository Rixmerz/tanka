# Tanka — turning Haiku into an assistant you can delegate to

Tanka is a **Claude Code harness** (plugin + clean workspace + launcher) that lets Claude Haiku act as a personal assistant: drafting and sending email, replying to messages, triaging inboxes and driving MCP servers, **without** the failure modes a small model is prone to — loops, unconfirmed actions, invented results, forgotten rules and degradation from too many tools.

It does not change the model. It changes the environment the model runs in. The rules that matter are enforced by hooks that deny or ask for confirmation, not by the prompt — the prompt is exactly what Haiku loses as context grows.

- Research behind it (official docs, GitHub issues, forums): [`docs/research/`](docs/research/)
- Architecture and the limitation → mitigation map: [`docs/architecture.md`](docs/architecture.md)

## What's in the box

| Piece | Purpose |
|---|---|
| `plugin/` | Hooks (per-class MCP tool policy, outgoing-message validation, loop guard, objectives, verified closure), the **Tanka MCP server** that serves your skills' tools, skills (`setup`, `plan`, `draft`, `triage`, `status`), the `tanka-verifier` agent, and an output style that pins the assistant role. |
| `builder/` | The `tanka-dev` plugin loaded by `tanka dev`: `new-skill`, `new-tool` and `new-subagent`, for building skills, tools and subagents on a stronger model. |
| `docs/tool-rules.md`, `docs/skill-rules.md`, `docs/subagent-rules.md` | The rules every tool, skill and subagent must follow, most of them enforced by `tanka tools check`. |
| `workspace-template/` | A clean directory with `.tanka/` (persona, policy, MCP allowlist, objectives) and a restrictive `.claude/settings.json`. |
| `bin/tanka` | `init`, `start`, `run`, `doctor`, `test`, `tools`, `dev`. Launches Claude Code in isolation: no user settings, no foreign MCP servers, no other plugins, no browser. |
| `tests/` | 81 tests for the hooks, the tool rules, the executor and the MCP server, plus a fake MCP server for end-to-end runs. |

## Requirements

- Claude Code 2.1 or newer (`claude --version`), signed in.
- `python3` — the hooks use only the standard library.
- Linux, macOS or WSL. Bash for the launcher.

## Quick start

```bash
git clone https://github.com/Rixmerz/tanka
export PATH="$PWD/tanka/bin:$PATH"

tanka start work                    # create the workspace "work" (asks first) and open a new session
tanka dev work                      # build its skills and tools (your default model)
tanka work                          # reopen it: pick a previous session, or a new one if it has none
```

**One workspace per domain, each with its own 15 tools.** A workspace is named: `tanka <name>` reopens it, `tanka start <name>` opens a new session and, when the name does not exist yet, creates it after asking (a typo should not produce a new empty assistant). Named workspaces live in `~/.tanka/workspaces/` (`TANKA_WORKSPACES`), as directories or as symlinks to a workspace kept elsewhere; a symlink is resolved to the real path, because Claude Code keys a project's session history by it. Every command that takes a directory also takes a name, and a path still works. Command names (`init`, `start`, `run`, `tools`…) cannot be workspace names.

Separate workspaces also separate what each assistant can reach: one that can message your clients has no tool that grades students, and the other way round.

**Routines and triggers** run the assistant on a clock or when a message arrives, unattended: it reads and drafts, and replies on its own only to the recipients you opt in. See [`docs/automation.md`](docs/automation.md).

**Every workspace has an advisor**: when a request needs more judgment than its tools give, the assistant can consult a stronger model (Sonnet by default, `advisorModel` in the workspace's `.claude/settings.json`).

**Authoring** (`tanka dev`) loads the [Rastro](https://github.com/Rixmerz/rastro) browser plugin and a `map-site` skill: map a website by hand, then turn it into headless calls a tool can make.

**The page.** `tanka ui [name]` opens a local page: a chat with each workspace's assistant, what its routines and triggers reported, and what the day cost, plus whatever its modules add ([how it works and how a module plugs in](docs/page.md)). A selector over the chat sends a message to **Dev** instead, your strong model, which builds boards, lenses and simple tools for that workspace within limits a hook enforces, and they show on the page at once. Its **Workspace** tab gives each assistant a picture and a name, shows the tools in use out of 15, and installs and removes modules or turns your own skills off and on (`tanka uninstall <module> <name>` does the same from a terminal). It listens on 127.0.0.1 only and needs the token in the URL it prints.

**Optional modules** live in [`modules/`](modules/). [`modules/desk`](modules/desk/README.md) keeps your day: reminders at a time and pending checks per topic that the assistant adds and ticks, shown beside the chat with a daily brief. [`modules/boards`](modules/boards/README.md) gives the assistant tables to fill (a course's grades and feedback per student, a client pipeline), grouped and counted on the page, each declared as a view that becomes its own typed tool. [`modules/whatsapp`](modules/whatsapp/README.md) lets a workspace read and answer WhatsApp for the contacts you assign to it and look at images and files; add it to a workspace with `tanka install whatsapp <name>` and link the phone with `tanka whatsapp link`. [`modules/codepanion`](modules/codepanion/README.md) watches your own Claude Code sessions and speaks first when one of the lenses you built with `/new-codepanion` says so, and out of the box guards force pushes, secrets in commits and the commit identity, and turns what a session leaves open into cards; it never touches code ([design](docs/codepanion.md)). `tanka codepanion setup <name> <project>` sets it up in one command. [`modules/cauce`](modules/cauce/README.md) puts the coding tasks [cauce](https://github.com/Rixmerz/cauce) runs on the page, in a **Code** tab, and lets the assistant check them and queue new ones in the repositories you allow (`tanka cauce allow <name> <directory>`); only you start the queue. `tanka modules` lists them.

**The first session asks one question: which language should the assistant work in.** Answer in the language you want — the reply itself is the answer — and it is saved to `.tanka/persona.json`. Everything the assistant writes from then on is in that language. The repository's own files stay in English by design.

Everything else about the profile is optional. Name, tone, signature, timezone and notes can all be skipped and filled in later, one at a time, at the moment they first matter: the harness asks for the signature right before your first email goes out, not in an onboarding questionnaire. `/tanka:setup` fills them all in one pass if you prefer that.

Inside the session:

```
/tanka:setup Kira                   # name, personality, language, signature…
/tanka:status                       # what is enabled, what is allowed, what is still unset
/tanka:plan take care of …          # objective with done-criteria before acting
/tanka:draft reply to Ana …         # draft → verify → confirm → send
/tanka:triage go through my inbox   # rubric-based classification with confidence
```

The first time you start in the workspace, accept Claude Code's directory trust dialog. Without it, the project's `permissions.allow` rules are ignored (the harness still enforces its own policy through hooks).

### Skills and tools: the only things the assistant can use

The assistant acts through **tools you define**, grouped under **skills**, and nothing else:

```
~/tanka-workspace/.claude/skills/library/
├── SKILL.md                         which tool answers which request
└── tools/
    ├── library_loans.json            manifest: description, typed params, examples, effect, command
    ├── library_loans.py              the command it runs
    └── ...
```

- Tanka's own MCP server serves every valid manifest as `mcp__tanka__<tool>`. Tools are **pre-approved**: no confirmation prompt. The skill decides when the assistant confirms with the user first.
- A tool's name starts with its skill's prefix (`library_*`), declares an effect (`read`, `draft`, `modify`, `send`; never destructive), and fits hard limits: 15 tools in total, 6 per skill, 6 params. The limits and the reasons are in [`docs/tool-rules.md`](docs/tool-rules.md) and [`docs/skill-rules.md`](docs/skill-rules.md).
- **Subagents** are tools whose work a stronger model does (`opus`, `sonnet`, with their own effort and dollar cap): Haiku calls one for the expensive step, such as reviewing work against a rubric, and acts on the proposal itself. They run outside the session, confined, and never publish. See [`docs/subagent-rules.md`](docs/subagent-rules.md) and the [worked grading example](docs/examples/grading-with-a-subagent.md).
- **Build them with `tanka dev`**, which opens your normal Claude Code (your default model) on the repo with the `new-skill`, `new-tool` and `new-subagent` skills. The assistant itself cannot write skills or tools.
- Check and try them without Claude:

```bash
tanka tools check                   # every rule, per tool and per skill
tanka tools test library_loans '{}' # run one exactly as the assistant would
```

Every turn the harness reminds the assistant of its skills and tools and tells it to say so, not improvise, when none fits.

Foreign MCP servers, Claude in Chrome, web search and non-Tanka subagents are blocked. To go back to loading `.tanka/mcp.json` with the name-based policy below, set `"external_mcp": "policy"` in `policy.json`.

Plugins: none except Tanka. `--setting-sources project,local` leaves your user-level plugins out.

### Persona and format

`.tanka/persona.json` (or `/tanka:setup`): `name`, `user_name`, `language`, `tone`, `personality`, `output_format`, `signature`, `timezone`, `notes`.

An empty string means "not set yet". The harness never nags about those: it lists them once at session start and asks for one only when the current task needs it. `configured: false` is what triggers the language question on the first run.

The assistant role and the safety invariants are not editable from there — they live in `plugin/output-styles/tanka.md` and in the hooks.

### Policy

`.tanka/policy.json` partially overrides the defaults in `plugin/scripts/tanka_common.py`:

```json
{
  "decisions": { "read": "allow", "draft": "allow", "modify": "ask", "send": "ask", "destructive": "deny", "unknown": "ask" },
  "send_validation": { "recipient_allowlist": ["@mycompany\\.com$"], "internal_domains": ["mycompany.com"] },
  "loop_guard": { "max_identical_calls_per_turn": 2, "max_calls_per_turn": 25, "max_consecutive_failures": 3 }
}
```

MCP tools are classified by name (`mcp__<server>__<tool>`): `delete|trash|remove…` → destructive (always denied), `send|reply|forward|post|share…` → send (validated, then confirmed), `label|archive|update|move…` → modify (confirmed), `get|list|search|read…` → read (free). Anything else is `unknown` → confirmed. Adjust `tool_classes` if your server uses different naming.

### Delegated tasks, no human in the loop

```bash
tanka run "Triage today's unread mail and leave drafts for the urgent ones" \
  ~/tanka-workspace --objective triage-inbox --max-turns 20 --budget 0.50
```

Fail-closed mode: anything that would ask for confirmation is denied, `may_send` is forced to `false`, and both turns and spend are capped. It can read, classify and leave drafts; it can never send or delete.

## How it mitigates each Haiku limitation

| Observed limitation | Mechanism |
|---|---|
| Repeats a call even after the tool says it is already done | `PreToolUse` denies the third identical call in a turn; per-turn and per-session action budgets; three consecutive failures stop the retry |
| Invents parameters (recipient, date) instead of asking | Outgoing-message validation: placeholders, minimum body, secrets, blocked or non-allowlisted recipients |
| Claims "sent" without having called the tool | The `Stop` hook blocks closure without a successful `tool_result` of that class |
| Forgets rules in long conversations | Hard rules re-injected every turn; objective persisted to a file; forced output style |
| Degrades with many tools / 200K without compaction | Clean workspace, `--strict-mcp-config`, warning above 3 servers, low `MAX_MCP_OUTPUT_TOKENS` |
| Overreaches (commits, sends, deletes) | `send` and `modify` require confirmation; `destructive` is denied; `may_send` per objective |
| Follows instructions embedded in email | External content is treated as data, and no irreversible action is possible without a human |

**Always open the workspace through `tanka`.** A plain `claude` or `claude -r` inside it has none of Tanka's tools or guardrails and loads your own plugins, so the workspace's settings refuse its first prompt and point to `tanka <workspace name>`.

## Development

```bash
tanka test                          # 147 tests
tanka tools check                   # the workspace's tools and skills
claude plugin validate plugin --strict
tanka doctor ~/tanka-workspace
```

End-to-end runs against a simulated mailbox (`tests/fake_mcp_server.py`) are described in [`tests/README.md`](tests/README.md).

## Installing as a plugin (instead of the launcher)

```bash
claude plugin marketplace add Rixmerz/tanka
claude plugin install tanka@tanka --scope project
```

Without the launcher only the second isolation layer applies (project settings); your user-level MCP servers and plugins still load.

## Known limitations

- It does not make Haiku better at open-ended planning. `TANKA_MODEL=sonnet tanka start` swaps the driver model without changing anything else.
- Tool classification by name is a heuristic (only with `"external_mcp": "policy"`); review `tool_classes` for servers with opaque names. Tanka tools declare their class instead.
- Pre-approved `send` tools run without a harness prompt. A skill's confirmation step works when a value is missing or was looked up; when the user's own message carries every value, Haiku acts directly.
- Skills cannot use dynamic `` !`command` `` injection, because Bash is denied; they use `Read` instead.
- The unbacked-claim check matches phrasing in English and Spanish. Working in another language, add its patterns under `objective.claim_patterns` in `policy.json`; the closing `Status:` line is checked by its English keyword, which the assistant writes verbatim in any language.

## License

MIT — see [`LICENSE`](LICENSE).
