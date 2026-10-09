# Routines

Routines your assistant proposes and you approve. When you ask for something recurring ("summarise the open issues of PROJ every morning"), the assistant saves a **proposal**: a draft routine that does nothing until you activate it. Approving it writes the routine into the workspace's `.claude/automations.json`, where the automation daemon runs it like any routine you added with `tanka routine add` (see [docs/automation.md](../../docs/automation.md)).

The assistant never approves, never creates a trigger and never changes an existing automation: it cannot write `automations.json`, and its tools only add or withdraw its own pending proposals.

## Setup

```bash
tanka install routines <workspace>          # routines_list, routines_history, routines_propose, routines_withdraw
tanka routines proposals <workspace>        # what the assistant proposed, with the full task
tanka routines approve <workspace> <id>     # shows it in full, then asks "Activate it? [y/N]" (--yes skips the question)
tanka automation service install            # routines run while the daemon is up
```

Other commands: `tanka routines list|history <workspace>`, `reject <workspace> <id>`, `remove <workspace> <name>`. The page's Routines tab does the same.

## How it works

| Piece | What it does |
| --- | --- |
| Proposals | `proposals/<workspace>.json` under the module's home, outside every workspace. Each has an id, name, interval, task, budget, a reason and a status: pending, approved, rejected or withdrawn. The newest 20 are kept. |
| Tools | `routines_list` and `routines_history` read; `routines_propose` saves a pending proposal (never a trigger); `routines_withdraw` withdraws one of its own pending proposals. |
| Approval | Re-checks every limit, then adds the routine to `automations.json` with `"proposed_by": "assistant"`, so lists show who made it. |

Every rule is enforced in `routines.py`, not in the skill: the name (2-32 lowercase letters, digits and -), not already a routine, trigger or pending proposal; the interval; the budget; the task's length and no control characters; how many proposals may wait and how many automations a workspace may have. Proposals of one workspace are invisible to every other.

**Risk:** each run of an approved routine is a model call that costs up to its budget. A run is unattended: it reads and drafts, and sends nothing unless you opted a recipient in (docs/automation.md).

## Configuration

`limits.json` in the module's home, which the assistant cannot write; any key may be left out, and an invalid value is ignored:

| Key | Default | Meaning |
| --- | --- | --- |
| `max_automations` | `8` | Routines and triggers a workspace may have when a proposal is approved |
| `min_interval_seconds` | `3600` | The shortest interval a proposal may ask for |
| `max_budget_usd` | `0.5` | The highest budget per run a proposal may ask for |
| `max_pending` | `5` | Proposals that may wait for you at once |
| `max_task_chars` | `800` | The longest task |
| `approval` | `manual` | `auto`: a proposal starts at once, still inside every limit above. The page's Workspace tab → Autonomy switches it |

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_ROUTINES_HOME` | `~/.tanka/shared/routines` | Proposals and `limits.json` |
| `TANKA_ROUTINES_MAX_AUTOMATIONS`, `TANKA_ROUTINES_MIN_INTERVAL`, `TANKA_ROUTINES_MAX_BUDGET`, `TANKA_ROUTINES_MAX_PENDING`, `TANKA_ROUTINES_MAX_TASK_CHARS` | as above | The same limits; `limits.json` wins |

## Files

| File | What it does |
| --- | --- |
| `routines.py` | Proposals, limits, approval and the tools' output |
| `cli.py` | `tanka routines …` and `post-install` |
| `page.py`, `page.js` | The Routines tab of the page |
| `skill/` | `SKILL.md` and the four tools |

Tests: `tests/test_routines.py`.
