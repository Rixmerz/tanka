---
name: new-module
description: Create or change a Tanka module — a skill that ships with the repository and installs into any workspace with `tanka install` (WhatsApp, a mailbox, a calendar). Use when the user wants a capability other people or other workspaces can reuse, says "crea un módulo", "add a module", "que sirva para cualquier workspace", or names a service everybody has (Gmail, Outlook, Telegram, a calendar).
argument-hint: "<name> <what it should do>"
---

# New Tanka module

A module is code strangers will run, in workspaces you will never see. Read `docs/module-rules.md` completely, then `docs/tool-rules.md` and `docs/skill-rules.md`: the module's skill follows all three. `modules/whatsapp/` is the reference implementation; copy its shape.

## 1. Decide it is a module

Ask, one question at a time, only what you cannot find out:

- What should the assistant do with it? Two or three real phrasings.
- Is it generic? A service anyone has, nothing about one person or organisation. If it is specific, make a workspace skill instead (`new-skill`) and stop here.
- What must each workspace be kept from? That is the scope rule (module-rules §3): who decides it, and where that data lives.

Run `tanka modules` first: extend an existing module instead of making a second one for the same service.

## 2. Find the mechanism, and prove it by hand

Prefer an official API the user already has access to; otherwise a logged-in page driven by Rastro, headless, reading what the page already holds. Prove every step by hand before writing the library: list, read one item, and, if it writes, one write to the author's own account. Note what fails (headless refused, links that expire, a signature that changed) — each becomes a line in the README.

## 3. Build it

1. `modules/<name>/module.json`, then the library `<name>.py`: settings from `TANKA_<NAME>_*` with defaults, scope checks first, one function per tool, messages in English that say what to do next.
2. `skill/tools/`: one thin script per tool (`SCOPE = "__SCOPE__"`, import the library, call one function) and its manifest. At most 6 tools; 3–4 is better.
3. `skill/SKILL.md` in English: which tool, recipes, the confirmation step before any `send`, rules, out of scope.
4. `cli.py` if the user needs a command (link an account, check status), a `post-install` step (create the example data file), or `events` for the automation daemon (`"events"` in module.json).
5. `page.py` and `page.js` if it should show on the page (`tanka ui`): read `docs/page.md` first. A button that needs the model writes a message into the chat's box; the page never acts in the world.
6. `"needs"` in module.json when it cannot work without another module (its tools are installed first and count toward the 15).
7. `README.md`: what, setup, risk, scope, configuration table, files.
8. `tests/test_<name>.py`: scope refusals before any network call, parsers and crypto on fixtures, install into a temporary workspace (reuse `tests/workspace_case.py`, which keeps every home in a temporary directory).

## 4. Verify — all of it

1. `tanka modules check <name>` → ok.
2. `tanka test` → everything passes.
3. `tanka install <name> <scratch workspace>`, then `tanka tools test` every tool against the real service with the author's own account; a wrong argument names the field.
4. A real session: `tanka run "<phrasing>" <scratch workspace>` and read `.tanka/state/sessions/`.
5. Grep the diff for personal data before committing.

Report what was built, what each check returned, and what was not run (every `send` against anyone but the author).
