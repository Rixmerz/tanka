# Bitbucket module

Lets a Tanka assistant list Bitbucket Cloud repositories, read their recent commits, follow pipeline runs and read the end of a step's log, see which pipelines can be run, and run or stop a custom pipeline, only in the repositories the user assigned to that assistant. **By default it cannot act:** running or stopping a pipeline leaves a request that waits for your **Approve** click, unless you list that repository (and pipeline) as bypass.

It needs no token. You sign in to bitbucket.org once, in a visible window of the module's own headless [Rastro](https://github.com/Rixmerz/rastro) session; the tools then call Bitbucket's REST API 2.0 from inside that signed-in page, the way Bitbucket's own web app does (`https://bitbucket.org/!api/2.0/...`). Your cookies, and the CSRF token a run or a stop needs, are used inside the page and never reach the assistant, its output or a file. Python standard library only.

## Setup

```bash
tanka install approvals <workspace>     # the Approve buttons; without it every action is refused
tanka install bitbucket <workspace>     # add the bitbucket skill (5 tools) and an example scopes.json
export TANKA_BITBUCKET_WORKSPACE=acme   # or "workspace" in config.json (below)
tanka bitbucket login                   # a browser window on Bitbucket's sign-in page: sign in, then press Enter
tanka bitbucket status                  # the workspace, whether the session is signed in, what each workspace may do
```

`tanka bitbucket login` opens the session `bitbucket` (`TANKA_BITBUCKET_SESSION`) in a visible window, allowed to write only to bitbucket.org and the Atlassian and Google sign-in hosts, so you can sign in with your Atlassian account or with Google. When you press Enter the window closes; from then on the tools open the same session headless, allowed to write to bitbucket.org only. If a provider refuses to sign in inside an automated window, sign in with your Atlassian email and password instead.

When the sign-in expires, a tool says "The Bitbucket session has expired" and the assistant tells you to run `tanka bitbucket login` again; it does not retry. Whatever the signed-in account may do in Bitbucket, the session may do too: `scopes.json` is what keeps each assistant to its repositories.

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
| `repos` | a list of repository slugs, or `"*"` | what it may read; `"*"` is every repository the signed-in account sees, new ones included |
| `bypass` | `{slug: "*" or [pipeline names]}` | where it may run or stop a pipeline **at once**: `"*"` is any custom pipeline of that repository, a list is only those custom pipelines. Only repositories in `repos` count |
| `unattended_write` | `true` or `false` (default) | whether a routine or trigger may use the bypass (see below) |

Before touching the browser the library refuses: a workspace with no entry ("No Bitbucket repository is assigned to this assistant"); a repository outside `repos`; a branch, pipeline name or commit that is not well formed. Everything outside `bypass` sends nothing: the tool leaves the exact action with the [approvals](../approvals/README.md) module, and the chat shows it with **Approve** and **Dismiss**. Your click runs the skill's `approve.py`, which sends that one stored action, once, after checking again that the repository is still in `repos`.

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

Rate limits (429) and gateway errors (502, 503) are retried up to 4 times, honouring `Retry-After`. One assistant at a time drives the session (a lock beside `scopes.json`). Every output stays under 6000 characters.

**Risk:** the module automates Bitbucket's web app with your signed-in session, not an API token: it can break when Atlassian changes the app, and automating a work account may be against your organisation's policy or Atlassian's terms, so check before using it. Running a pipeline deploys or builds code with whatever the pipeline does, and others see it at once. Put in `bypass` only repositories and pipelines where a wrong run is cheap (a development deploy), never production.

## Configuration

| Variable | Default | What it sets |
| --- | --- | --- |
| `TANKA_BITBUCKET_HOME` | `~/.tanka/shared/bitbucket` | Where `scopes.json`, `config.json` and the session lock live |
| `TANKA_BITBUCKET_WORKSPACE` (`workspace`) | (required) | The Bitbucket workspace id, as in `bitbucket.org/<workspace>/` (e.g. `acme`) |
| `TANKA_BITBUCKET_SESSION` (`session`) | `bitbucket` | The Rastro session that holds the sign-in |
| `RASTRO_CHROMIUM` | `modules/common/chromium-headless` | The browser Rastro starts for the session (a real Chromium with a normal user agent) |

## The settings file

The workspace and the session's name can also live in `config.json`, beside `scopes.json` and outside every workspace. An environment variable always wins over the file. It holds no secret:

```json
{
  "workspace": "acme",
  "session": "bitbucket"
}
```

## Files

| File | What it does |
| --- | --- |
| `module.json` | Name and description, read by `tanka modules` |
| `bitbucket.py` | Scope and bypass, the browser session and the in-page API calls with retries, log redaction, the `bitbucket-pipelines.yml` reader, the operations, approval requests |
| `cli.py` | `tanka bitbucket login` and `status`, and `post-install` |
| `page.py`, `page.js` | Its part of the page: whether it is configured, the bypass list, the Autonomy switch |
| `config.json` | Yours, not shipped: the workspace and, optionally, the session's name |
| `skill/` | The skill `tanka install` copies: `SKILL.md`, five tools and `approve.py` |

Tests: `tests/test_bitbucket.py`.
