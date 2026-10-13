# Bitbucket module

Lets a Tanka assistant list Bitbucket Cloud repositories, read their recent commits, follow pipeline runs and read the end of a step's log, see which pipelines can be run, and run or stop a custom pipeline, only in the repositories the user assigned to that assistant. **By default it cannot act:** running or stopping a pipeline leaves a request that waits for your **Approve** click, unless you list that repository (and pipeline) as bypass.

It calls the Bitbucket Cloud REST API 2.0 (`https://api.bitbucket.org/2.0`) with a token, using the Python standard library only.

## Setup

```bash
tanka install approvals <workspace>     # the Approve buttons; without it every action is refused
tanka install bitbucket <workspace>     # add the bitbucket skill (5 tools) and an example scopes.json
export TANKA_BITBUCKET_WORKSPACE=acme
export TANKA_BITBUCKET_EMAIL=someone@example.com   # leave it out for an access token (Bearer)
export BITBUCKET_API_TOKEN=<API_TOKEN>             # or any name, see TANKA_BITBUCKET_TOKEN_VAR
tanka bitbucket status                  # what is configured and what each workspace may do
```

Two kinds of token work:

- **An Atlassian API token with scopes** (id.atlassian.com → Security → API tokens → *Create API token with scopes*, app Bitbucket), with `read:repository:bitbucket`, `read:pipeline:bitbucket`, `write:pipeline:bitbucket`, `read:workspace:bitbucket` and `read:user:bitbucket`. Set `email` to the account's email: it is sent as Basic `email:token`.
- **A workspace (or project, or repository) access token** (Bitbucket → workspace settings → Access tokens) with repository read and pipeline read and write. Leave `email` unset: it is sent as a Bearer token.

Keep it in your shell profile or secret manager, never in a file inside a workspace or the repository. The module reads it from the environment when a tool runs and never prints, logs or writes it. Leave out `write:pipeline` if the assistant should only read.

## What each assistant may read and do

`scopes.json`, in `TANKA_BITBUCKET_HOME`, is edited by the user only. It sits outside every workspace, where no assistant can write.

```json
{
  "scopes": {
    "work": {
      "repos": ["api-orders", "api-billing", "web-shop"],
      "bypass": {"api-orders": "*", "api-billing": ["deploy-dev"]},
      "unattended_write": false
    },
    "reader": {"repos": "*"}
  }
}
```

| Key | Value | Meaning |
| --- | --- | --- |
| `repos` | a list of repository slugs, or `"*"` | what it may read; `"*"` is every repository your token sees, new ones included |
| `bypass` | `{slug: "*" or [pipeline names]}` | where it may run or stop a pipeline **at once**: `"*"` is any custom pipeline of that repository, a list is only those custom pipelines. Only repositories in `repos` count |
| `unattended_write` | `true` or `false` (default) | whether a routine or trigger may use the bypass (see below) |

Before any network call the library refuses: a workspace with no entry ("No Bitbucket repository is assigned to this assistant"); a repository outside `repos`; a branch, pipeline name or commit that is not well formed. Everything outside `bypass` sends nothing: the tool leaves the exact action with the [approvals](../approvals/README.md) module, and the chat shows it with **Approve** and **Dismiss**. Your click runs the skill's `approve.py`, which sends that one stored action, once, after checking again that the repository is still in `repos`.

Stopping follows the same rule: with `"*"` the assistant may stop any run of that repository at once; with a list, only runs of those custom pipelines; anything else waits for Approve.

## Acting when nobody is watching

Tanka's own tools are pre-approved, so what stops a routine, a trigger or `tanka run` from deploying is this module, not a prompt. When `TANKA_UNATTENDED` is set (and the message did not come from the page's chat), even a bypass repository only leaves a request for your Approve click, unless its entry says `"unattended_write": true`; the page's Workspace tab → Autonomy switches it. Reading is never affected.

## Tools

| Tool | Effect | What it does |
| --- | --- | --- |
| `bitbucket_repos` | read | the repositories it may read (filter by name): slug, project, language, last update and whether it has bypass |
| `bitbucket_commits` | read | the latest commits of a repository, optionally on one branch: short hash, date, author, first line |
| `bitbucket_pipelines` | read | the recent runs; with `build_number`, its steps; with `step`, the last 150 lines of that step's log (filter with `grep`; credentials replaced by `***`); with `options`, the pipelines that can be run on a branch, read from its `bitbucket-pipelines.yml` and from the recent runs |
| `bitbucket_run_pipeline` | send | run a custom pipeline on a branch (optionally at a commit); outside bypass, a request for your Approve click |
| `bitbucket_stop_pipeline` | send | stop a running pipeline; outside bypass, a request for your Approve click |

Rate limits (429) and gateway errors (502, 503) are retried up to 4 times, honouring `Retry-After`. Every output stays under 6000 characters.

**Risk:** running a pipeline deploys or builds code with whatever the pipeline does, and others see it at once. Put in `bypass` only repositories and pipelines where a wrong run is cheap (a development deploy), never production. The token acts with all of its permissions; the scope file is what keeps each assistant to its repositories. Use a token you can revoke, with only the scopes above.

## Configuration

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_BITBUCKET_HOME` | `~/.tanka/shared/bitbucket` | Where `scopes.json` and `config.json` live |
| `TANKA_BITBUCKET_WORKSPACE` (`workspace`) | (required) | The Bitbucket workspace id, as in `bitbucket.org/<workspace>/` (e.g. `acme`) |
| `TANKA_BITBUCKET_EMAIL` (`email`) | (none) | The Atlassian account email for an API token (Basic); unset, the token is sent as Bearer |
| `TANKA_BITBUCKET_TOKEN_VAR` (`token_var`) | `BITBUCKET_API_TOKEN` | The **name** of the environment variable that holds the token |
| `TANKA_BITBUCKET_CA_FILE` (`ca_file`) | (none) | A CA bundle for a corporate proxy that re-signs TLS |

## The settings file

Everything above except the token can also live in `config.json`, beside `scopes.json` and outside every workspace. An environment variable always wins over the file. The file never holds a token:

```json
{
  "workspace": "acme",
  "email": "someone@example.com",
  "token_var": "MY_BITBUCKET_TOKEN",
  "token_wrapper": ["secrets-tool", "exec", "MY_BITBUCKET_TOKEN", "--"]
}
```

`token_wrapper` is for a token kept in a secrets manager instead of the environment. When the variable is missing, a tool runs its own script once more under that command, which injects the variable for that one short process. The wrapper must be a command that runs another command and exits when it does. If it is missing or fails, the tool ends in the usual "token is not set" error instead of looping.

## Files

| File | What it does |
| --- | --- |
| `module.json` | Name and description, read by `tanka modules` |
| `bitbucket.py` | Scope and bypass, HTTP with retries, log redaction, the `bitbucket-pipelines.yml` reader, the operations, approval requests |
| `cli.py` | `tanka bitbucket status`, and `post-install` |
| `page.py`, `page.js` | Its part of the page: whether it is configured, the bypass list, the Autonomy switch |
| `config.json` | Yours, not shipped: the workspace, email, token variable's name and an optional `token_wrapper` |
| `skill/` | The skill `tanka install` copies: `SKILL.md`, five tools and `approve.py` |

Tests: `tests/test_bitbucket.py`.
