---
name: map-site
description: Map a website with Rastro and turn what a person does on it into calls a Tanka tool can make headless — find the requests the page itself makes, then call them from inside the logged-in page or over plain HTTP instead of clicking. Use before building a tool or module for any site without an official API (a portal, a webmail, a messenger), when the user says "mapea", "usa rastro", "que Tanka pueda entrar a…", or when a tool that clicks through a page is slow or breaks.
argument-hint: "<site> <what the tool must do>"
---

# Map a site, then go headless

Rastro is the browser: `rastro <command>` (see the `rastro` skill). Every step below is done by you, by hand, before any tool code is written. Mask personal data (addresses, numbers, names) in anything you print while probing.

## 1. Session

- One Rastro session per site, named after it (`rastro -s <site>`). Sign in once in a **visible** window (`rastro -s <site> open <url> --headed`), then `rastro -s <site> close`: the profile keeps the login and tools reopen it headless.
- If the site refuses headless Chromium ("unsupported browser", a blank page), start it through `modules/common/chromium-headless` with `RASTRO_CHROMIUM` set to that path. It sends a normal user agent.
- Grant the least: `--allow-write <the site's own host>` only for what must write, `--allow-upload <dir>` only for folders a user chose. When a page answers with empty results, check the effect line for "writes blocked": searches often go out as POSTs.

## 2. Map: do it once as a person would

1. `rastro view` / `rastro read` to see the page; `rastro act <ref> <kind>` to do the task step by step.
2. After each action, `rastro effects <n>` and `rastro trace --action <n>` list the requests it caused; `rastro request <id>` shows one (headers, body, `--curl`). Look for JSON or feed endpoints: that is the site's own API.
3. In the page, `rastro eval` finds what the app already holds: global config objects, its module system (`window.require`, webpack chunks), storage keys. Print shapes and key names, never values of tokens or cookies.

## 3. Choose the headless path, best first

1. **A saved Rastro routine** replayed over HTTP (`rastro flow record` / `routine run`), when the task is a fixed form submission.
2. **The page's own endpoints, called from inside the page** with `rastro eval` and `fetch(..., {credentials: "include"})`, using the auth the page already has (a cookie, a token in its storage). The credential never leaves the page and never reaches the model.
3. **The app's in-memory objects** (its collections, its own send or load functions) when the endpoints are private or signed.
4. **The DOM**, read only from visible elements, and **UI actions** last: they are the slowest and break first.

## 4. Traps already paid for

- Changing only the URL hash inside a single-page app may not run the view; load the URL fully (`rastro goto`).
- Hidden views stay in the DOM: select only visible elements (`offsetParent`, bounding box).
- `DOMParser`/`innerHTML` can be blocked by Trusted Types: cut strings with a regex and parse in Python.
- Hidden file inputs and `aria-hidden` ancestors are invisible to Rastro: show the input for one upload, give it an `aria-label`, and let `rastro act <ref> upload` fill it, so Rastro's upload allowlist still applies.
- UI labels are localised: match the visible language and a stable class or attribute.
- Signed or expiring links (CDNs, attachments): fetch right away; report expiry instead of guessing.
- An internal function's signature changes between releases: try the new form, fall back to the old, and note the date in a comment.
- Some "view source" pages drop attachment bodies: look for the "download original" link.
- Combo boxes in old frameworks (ExtJS) confirm only on a full mouse sequence (`mousedown`, `mouseup`, `click`), not on `setValue`.

## 5. Freeze it

Put each proved call in a library function with its settings from environment variables, one tool per user intent, and the checks the rules require (`docs/tool-rules.md`; for something others can reuse, `docs/module-rules.md` and the `new-module` skill). Re-run every step through the library, then through `tanka tools test`, before calling it done.
