# Jira module

Lets a Tanka assistant search, read, create, transition, comment on and link Jira Cloud issues, only in the projects the user assigned to that assistant. Several workspaces can share one Jira site; each sees only its own projects.

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
    "personal": {"projects": ["HOME"], "write": false}
  }
}
```

Before any network call the library refuses: an issue key or project outside the workspace's `projects`; a JQL query that could escape them (every query is sent as `project in (...) AND (<query>)`, with `ORDER BY` kept at the end, and unbalanced parentheses or a misplaced `ORDER BY` are rejected); any create, transition, comment or link when `write` is not `true`; a link where either issue is outside the scope.

## Writing when nobody is watching

Tanka's own tools are pre-approved, so what stops a routine, a trigger or `tanka run` from creating or commenting is this module, not a prompt. A write is refused whenever `TANKA_UNATTENDED` is set, unless the workspace's entry in `scopes.json` also says `"unattended_write": true` (and `"write": true`). Reading is never affected. In a session you are in, the confirmation before a change comes from the skill's instructions; the hard limit is `write` in `scopes.json`.

## Tools

| Tool | Effect | What it does |
| --- | --- | --- |
| `jira_search` | read | JQL search, one line per issue |
| `jira_issue` | read | one issue with description, links and the last 10 comments |
| `jira_create` | modify | a new issue, optionally with labels and a parent |
| `jira_transition` | modify | move an issue to a status by name |
| `jira_comment` | send | add a comment; the skill asks for an explicit yes first |
| `jira_link` | modify | link two issues (`from_key blocks to_key`) |

Nothing deletes. Rate limits (429) and gateway errors (502, 503) are retried up to 4 times, honouring `Retry-After`. Every output stays under 6000 characters.

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
| `jira.py` | Scope, JQL wrapping, ADF conversion, HTTP with retries, the six operations |
| `cli.py` | `tanka jira status`, and `post-install` |
| `config.json` | Yours, not shipped: the site, email, token variable's name and an optional `token_wrapper` (see above) |
| `skill/` | The skill `tanka install` copies: `SKILL.md` and six tools |

Tests: `tests/test_jira.py`.
