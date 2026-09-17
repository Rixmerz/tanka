# Tanka — turning Haiku into an assistant you can delegate to

Tanka is a **Claude Code harness** (plugin + clean workspace + launcher) that lets Claude Haiku act as a personal assistant: drafting and sending email, replying to messages, triaging inboxes and driving MCP servers, **without** the failure modes a small model is prone to — loops, unconfirmed actions, invented results, forgotten rules and degradation from too many tools.

It does not change the model. It changes the environment the model runs in. The rules that matter are enforced by hooks that deny or ask for confirmation, not by the prompt — the prompt is exactly what Haiku loses as context grows.

- Research behind it (official docs, GitHub issues, forums): [`docs/research/`](docs/research/)
- Architecture and the limitation → mitigation map: [`docs/architecture.md`](docs/architecture.md)

## What's in the box

| Piece | Purpose |
|---|---|
| `plugin/` | Hooks (per-class MCP tool policy, outgoing-message validation, loop guard, objectives, verified closure), skills (`setup`, `plan`, `draft`, `triage`, `status`), the `tanka-verifier` agent, and an output style that pins the assistant role. |
| `workspace-template/` | A clean directory with `.tanka/` (persona, policy, MCP allowlist, objectives) and a restrictive `.claude/settings.json`. |
| `bin/tanka` | `init`, `start`, `run`, `doctor`, `test`. Launches Claude Code in isolation: no user settings, no foreign MCP servers, no other plugins. |
| `tests/` | 52 hook tests driven by simulated hook input, plus a fake MCP server for end-to-end runs. |

## Requirements

- Claude Code 2.1 or newer (`claude --version`), signed in.
- `python3` — the hooks use only the standard library.
- Linux, macOS or WSL. Bash for the launcher.

## Quick start

```bash
git clone https://github.com/Rixmerz/tanka
export PATH="$PWD/tanka/bin:$PATH"

tanka init ~/tanka-workspace        # create the clean workspace
tanka start ~/tanka-workspace       # interactive session, Haiku, isolated
```

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

### Enable only what you need

1. **MCP servers**: edit `~/tanka-workspace/.tanka/mcp.json` and add only what the task requires (see `mcp.example.json`). The launcher passes `--strict-mcp-config`, so nothing else loads. With Haiku, more than ~3 servers or ~15 tools degrades tool selection; the `SessionStart` hook warns you.
2. **Your own skills**: copy assistant-type skills (writing, classification, processes) to `~/tanka-workspace/.claude/skills/<name>/SKILL.md`. Skills in `~/.claude/skills` are not loaded.
3. **Plugins**: none except Tanka. `--setting-sources project,local` leaves your user-level plugins out.

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

## Development

```bash
tanka test                          # 52 hook tests
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
- Tool classification by name is a heuristic; review `tool_classes` for servers with opaque names.
- Skills cannot use dynamic `` !`command` `` injection, because Bash is denied; they use `Read` instead.
- The unbacked-claim check matches phrasing in English and Spanish. Working in another language, add its patterns under `objective.claim_patterns` in `policy.json`; the closing `Status:` line is checked by its English keyword, which the assistant writes verbatim in any language.

## License

MIT — see [`LICENSE`](LICENSE).
