# Jira module

Lets a Tanka assistant search, read, create, update, comment on and link Jira Cloud issues, only in the projects the user assigned to that assistant. Several workspaces can share one Jira site; each sees only its own projects.

It calls the Jira Cloud REST API v3 with an API token, using the Python standard library only.

## Setup

```bash
tanka install jira <workspace>      # add the jira skill (6 tools) and an example scopes.json
export TANKA_JIRA_SITE=https://your-company.atlassian.net
export TANKA_JIRA_EMAIL=someone@example.com
export JIRA_API_TOKEN=<API_TOKEN>   # or any name, see TANKA_JIRA_TOKEN_VAR
tanka jira status                   # what is configured and which projects each workspace may use
```

Create the token at id.atlassian.com, under Security, API tokens. Keep it in your shell profile or secret manager, never in a file inside a workspace or the repository. The module reads it from the environment when a tool runs and never prints, logs or writes it.

## Which projects each assistant can use

`scopes.json`, in `TANKA_JIRA_HOME`, is edited by the user only. It sits outside every workspace, where no assistant can write.

```json
{
  "scopes": {
    "work": {"projects": ["PROJ", "OPS"], "write": true},
    "personal": {"projects": ["HOME"], "write": false},
    "assistant": {"projects": "*", "write": ["PROJ"], "write_on_approval": "*"}
  }
}
```

| Key | Value | Meaning |
| --- | --- | --- |
| `projects` | a list of keys, or `"*"` | what it may read; `"*"` is every project your token sees, new ones included |
| `write` | `true`, `false`, a list, or `"*"` | where it may change issues directly; `true` is every project in `projects` |
| `write_on_approval` | the same | where a change waits for your **Approve** click in the chat (needs `tanka install approvals <workspace>`) |

Before any network call the library refuses: an issue key or project outside the workspace's `projects`; a JQL query that could escape them (with a list, every query is sent as `project in (...) AND (<query>)`, with `ORDER BY` kept at the end, and unbalanced parentheses or a misplaced `ORDER BY` are rejected); any create, update, comment, link or delete request in a project that is neither in `write` nor in `write_on_approval`; a link where either issue is outside the scope.

A change in a project that is only in `write_on_approval` sends nothing: the tool leaves the exact change with the [approvals](../approvals/README.md) module, and the chat shows it with **Approve** and **Dismiss**. Your click runs the skill's `approve.py`, which sends that one stored change, once, with the same scope checks.

## Writing when nobody is watching

Tanka's own tools are pre-approved, so what stops a routine, a trigger or `tanka run` from creating or commenting is this module, not a prompt. A write is refused whenever `TANKA_UNATTENDED` is set, unless the workspace's entry in `scopes.json` also says `"unattended_write": true` (and the project is in `write`); the page's Workspace tab → Autonomy switches it for the workspace. Reading is never affected. In a session you are in, the confirmation before a change comes from the skill's instructions; the hard limits are `write` and `write_on_approval` in `scopes.json`. A routine may still leave a change for your Approve click: nothing reaches Jira until you press it.

## Tools

| Tool | Effect | What it does |
| --- | --- | --- |
| `jira_search` | read | JQL search, one line per issue; with `key`, one issue with description, links and the last 10 comments; with neither, the projects the workspace may use and what it may do in each |
| `jira_create` | modify | a new issue, optionally with labels and a parent |
| `jira_update` | modify | move an issue to a status by name and change its summary, description or labels (labels are added or removed, never replaced) |
| `jira_comment` | send | add a comment; the skill asks for an explicit yes first |
| `jira_link` | modify | link two issues (`from_key blocks to_key`) |
| `jira_delete` | modify | asks to delete an issue: it leaves a request and the chat shows **Delete** and **Keep**; only the user's click deletes, for good |

The assistant never deletes: `jira_delete` writes a request in `deletions/<workspace>.json` (beside `scopes.json`), and the page's Delete button runs `confirm.py`, which uses your token wrapper like the tools. Jira refuses to delete an issue with subtasks. Rate limits (429) and gateway errors (502, 503) are retried up to 4 times, honouring `Retry-After`. Every output stays under 6000 characters.

**Risk:** the token acts with all of your Jira permissions; the scope file is what keeps each assistant to its projects. Use a token you can revoke, and `"write": false` where reading is enough.

## Configuration

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_JIRA_HOME` | `~/.tanka/shared/jira` | Where `scopes.json` and `config.json` live |
| `TANKA_JIRA_SITE` (`site`) | (required) | The Jira Cloud site, e.g. `https://your-company.atlassian.net` |
| `TANKA_JIRA_EMAIL` (`email`) | (required) | The Atlassian account email the token belongs to |
| `TANKA_JIRA_TOKEN_VAR` (`token_var`) | `JIRA_API_TOKEN` | The **name** of the environment variable that holds the API token |
| `TANKA_JIRA_CA_FILE` (`ca_file`) | (none) | A CA bundle for a corporate proxy that re-signs TLS |

## The settings file

Everything above except the token can also live in `config.json`, beside `scopes.json` and outside every workspace. An environment variable always wins over the file. The file never holds a token:

```json
{
  "site": "https://your-company.atlassian.net",
  "email": "someone@example.com",
  "token_var": "MY_JIRA_TOKEN",
  "token_wrapper": ["secrets-tool", "exec", "MY_JIRA_TOKEN", "--"]
}
```

`token_wrapper` is for a token that is kept in a secrets manager instead of the environment. When the variable is missing, a tool runs its own script once more under that command, which injects the variable for that one short process. Tanka's MCP server and Claude Code keep running without the secret. The wrapper must be a command that runs another command and exits when it does, and its output must not be needed live: a wrapper that buffers output is fine here, because every tool call is short. If the wrapper is missing or fails, the tool ends in the usual "token is not set" error instead of looping.

## Files

| File | What it does |
| --- | --- |
| `module.json` | Name and description, read by `tanka modules` |
| `jira.py` | Scope, JQL wrapping, ADF conversion, HTTP with retries, the operations, approval requests |
| `cli.py` | `tanka jira status`, and `post-install` |
| `config.json` | Yours, not shipped: the site, email, token variable's name and an optional `token_wrapper` (see above) |
| `skill/` | The skill `tanka install` copies: `SKILL.md`, six tools and `approve.py` |

Tests: `tests/test_jira.py`.
