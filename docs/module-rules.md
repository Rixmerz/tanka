# Module rules

A module is a skill built once and installed into any number of workspaces: WhatsApp, a mailbox, a calendar. It lives in the repository, under `modules/<name>/`, so anyone who clones Tanka can use it; a workspace-only skill (see [skill-rules.md](skill-rules.md)) stays in that workspace.

Everything in [tool-rules.md](tool-rules.md) and [skill-rules.md](skill-rules.md) applies to the skill a module ships. This page adds what is different about code that strangers will run. Rules marked **[checked]** are enforced by `tanka modules check <name>` (`plugin/scripts/tanka_modules.py`).

## 1. Make a module or a workspace skill?

Make a module when the same capability is useful to people other than you, and nothing in it depends on who you are: a service everybody has (a mail provider, a messenger), a file format, a public API.

Keep it a workspace skill when it encodes one person's or one organisation's specifics: their university's portal, their course ids, their grading rubric.

## 2. Layout

```
modules/<name>/
├── module.json      {"name", "description", "requires": [programs on PATH]}   [checked]
├── README.md        what it does, setup, risks, configuration table          [checked]
├── <name>.py        the library: every rule and every call lives here
├── cli.py           optional: `tanka <name> <command>`, and `post-install`
└── skill/           copied into a workspace by `tanka install`                  [checked]
    ├── SKILL.md
    └── tools/       thin scripts that import the library and set SCOPE
```

- **[checked]** The directory name, `module.json`'s `name` and the skill's name are the same, lowercase, and not a `tanka` command. The tools' prefix follows from it (`whatsapp` → `whatsapp_*`).
- **[checked]** The skill passes `tanka tools check` in an empty workspace, within the same budget as any skill: at most 6 tools. Fewer is better: a module competes for the 15 slots of every workspace it is installed in.

## 3. Scope

A module installed in two workspaces must not let one assistant reach what belongs to the other.

- **[checked]** Every tool script contains `__SCOPE__`. `tanka install` replaces it with the workspace's name (or `--scope`), and the script passes it to the library on every call.
- The library decides what a scope may see, from data the **user** controls and **no assistant can write**: a file under `~/.tanka/shared/<name>/` (outside every workspace, so the assistants' Write allowlist cannot reach it), never a file in the workspace.
- Refuse before touching the network or the browser. A test proves it (see WhatsApp's `RegistryCase`).

## 4. Generic and configurable

- Nothing specific to one user in code, docs, examples or tests: no names, numbers, addresses, account ids or employers. Examples use `+15551234567`, `someone@example.com`, "Clara Client".
- Every path, limit and name has a default and an environment variable, `TANKA_<NAME>_<SETTING>`, listed in the README's configuration table.
- User data lives in `~/.tanka/shared/<name>/` (`TANKA_<NAME>_HOME`); what the assistant keeps per workspace lives in the workspace (`notes/…`).
- `requires` lists every program the module calls. `tanka install` warns when one is missing.
- Standard library only in the tool path, as for any tool. A heavy optional dependency gets its own venv and a setup command, and the module works without it.

## 5. The browser and outside services

- Drive browsers through Rastro sessions named after the module (`TANKA_<NAME>_SESSION`), headless. Sites that refuse headless Chromium can reuse `modules/common/chromium-headless` through `RASTRO_CHROMIUM`. Code two modules need goes in `modules/common/` (not a module: no `module.json`), never copied.
- Credentials never pass through the model, the tool output or a file the module writes. Use what the logged-in page already holds, from inside the page.
- Say in the README, in a **Risk** line, when automating the service is against its terms.
- One assistant at a time per session: take a lock (see `wa.session()`).

## 6. Language

Everything in the module is English: code, messages, CLI output, the shipped skill and its tool descriptions. The assistant answers in the user's language regardless. A workspace may later translate its installed copy; the module stays English.

## 7. Before calling it done

1. `tanka modules check <name>` → `ok`.
2. Tests in `tests/test_<name>.py`: scope refusals, every parser and crypto routine on fixtures, `tanka install` into a temporary workspace. No network, no browser. `tanka test` passes.
3. `tanka install <name> <scratch workspace>`, then `tanka tools test` each tool against the real service with the author's own account.
4. `send` tools: only against the author's own address or number, never someone else, and say so.
5. Scan the diff for personal data (names, numbers, addresses, ids, paths under a home directory) before committing.
