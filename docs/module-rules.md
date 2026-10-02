# Module rules

A module is a skill built once and installed into any number of workspaces: WhatsApp, a mailbox, a calendar. It lives in the repository, under `modules/<name>/`, so anyone who clones Tanka can use it; a workspace-only skill (see [skill-rules.md](skill-rules.md)) stays in that workspace.

Everything in [tool-rules.md](tool-rules.md) and [skill-rules.md](skill-rules.md) applies to the skill a module ships. This page adds what is different about code that strangers will run. Rules marked **[checked]** are enforced by `tanka modules check <name>` (`plugin/scripts/tanka_modules.py`).

## 1. Make a module or a workspace skill?

Make a module when the same capability is useful to people other than you, and nothing in it depends on who you are: a service everybody has (a mail provider, a messenger), a file format, a public API.

Keep it a workspace skill when it encodes one person's or one organisation's specifics: their university's portal, their course ids, their grading rubric.

## 2. Layout

```
modules/<name>/
├── module.json      {"name", "description", "requires", "needs", "events"}     [checked]
├── README.md        what it does, setup, risks, configuration table          [checked]
├── <name>.py        the library: every rule and every call lives here
├── cli.py           optional: `tanka <name> <command>`, `post-install`, `events`
├── page.py, page.js optional: its part of the page, `tanka ui` (docs/page.md)
└── skill/           copied into a workspace by `tanka install`                  [checked]
    ├── SKILL.md
    └── tools/       thin scripts that import the library and set SCOPE
```

- **[checked]** The directory name, `module.json`'s `name` and the skill's name are the same, lowercase, and not a `tanka` command. The tools' prefix follows from it (`whatsapp` → `whatsapp_*`).
- `requires` lists programs on PATH; **[checked]** `needs` lists other modules this one cannot work without (the codepanion needs the desk). `tanka install` installs those first, if the workspace lacks them, and counts their tools toward the 15.
- `events` (`{"poll_seconds", "description", "always"}`) makes the automation daemon run `cli.py events` at that pace; each line it prints is a JSON event a trigger can listen to (`--on <name>:<value>`). `"always": true` polls it even with no trigger, for a poll that does work of its own (the desk fires reminders there). The poll runs every few seconds for every user: it must be fast, need no model, and never write the user's settings.
- `tanka install` writes `.module.json` into the copied skill, which is how the page and `tanka uninstall` tell a module's skill from one the user made. `tanka uninstall` removes the skill (its tools) and nothing else; a `cli.py` that handles `post-remove <ws> <scope>` is called afterwards. A module's page panel follows its `installed()` rule, which may look at data rather than at the skill.
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

## 6. The page

A module adds to the page with `page.py` (state, chat items, chips under an answer, context for the next message, a hint naming only its own tools, actions) and `page.js` (tabs, how its items look). The contract is in [docs/page.md](page.md). Two rules matter most:

- The page's actions change only what the user may change by hand (tick, rate, archive). Anything that acts in the world is a message the user sends in the chat, and the assistant does it with its tools, through the same rules as any other request. A button that needs a model writes its message into the chat's box (`Tanka.compose`) and never sends it.
- `page.js` builds every element with `el()` and sets text as text. No `innerHTML`, no `eval`: the page has one script and one nonce, and a module's code runs inside it.

## 7. Language

Everything in the module is English: code, messages, CLI output, the shipped skill and its tool descriptions. The assistant answers in the user's language regardless. A workspace may later translate its installed copy; the module stays English.

## 8. Before calling it done

1. `tanka modules check <name>` → `ok`.
2. Tests in `tests/test_<name>.py`: scope refusals, every parser and crypto routine on fixtures, `tanka install` into a temporary workspace. No network, no browser. `tanka test` passes.
3. `tanka install <name> <scratch workspace>`, then `tanka tools test` each tool against the real service with the author's own account.
4. `send` tools: only against the author's own address or number, never someone else, and say so.
5. Scan the diff for personal data (names, numbers, addresses, ids, paths under a home directory) before committing.
