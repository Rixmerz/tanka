"""Gmail for Tanka: read the mailboxes signed in to one browser session, per workspace.

One Rastro session (`gmail`, headless) holds every Google account the user
signed in to; Gmail serves them as /mail/u/0/, /mail/u/1/, ... What each
assistant may read is decided here, never by the model:

- `accounts.json` (in TANKA_GMAIL_HOME) maps an address to the one workspace
  allowed to read it. The user edits it; the assistants cannot, because they
  only write inside their own workspace.
- An address that is not in the file, or that belongs to another workspace,
  is never read.

Messages are fetched as their original MIME source from inside the logged-in
page and parsed with the standard library; reading marks nothing as read.
Replies and new mail go through Gmail's own compose window. Rastro lets the
session write only to mail.google.com and upload only from the workspace and
TANKA_GMAIL_UPLOAD_DIRS, so a message cannot talk the assistant into attaching
an arbitrary file from the disk.
"""
import contextlib
import email
import email.policy
import fcntl
import html
import json
import os
import re
import secrets
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import office  # noqa: E402  (modules/common)

MODULE = Path(__file__).resolve().parent
# Every setting has a default and an environment variable; see README.md.
HOME = Path(os.environ.get("TANKA_GMAIL_HOME", Path.home() / ".tanka" / "shared" / "gmail"))
REGISTRY = HOME / "accounts.json"
LOCK = HOME / ".lock"
SESSION = os.environ.get("TANKA_GMAIL_SESSION", "gmail")
ATTACH_DIR = os.environ.get("TANKA_GMAIL_ATTACH_DIR", "gmail")
MAX_BYTES = int(os.environ.get("TANKA_GMAIL_MAX_MB", "25")) * 1024 * 1024
BODY_CAP = int(os.environ.get("TANKA_GMAIL_BODY_CHARS", "4000"))
MAX_ACCOUNTS = 10
# Extra folders attachments may be taken from, besides the workspace (":"-separated).
UPLOAD_DIRS = [d for d in os.environ.get("TANKA_GMAIL_UPLOAD_DIRS", "").split(":") if d]
WRAPPER = MODULE.parent / "common" / "chromium-headless"
BASE = "https://mail.google.com"
TINY_INLINE = 15 * 1024  # inline pictures below this are signatures and logos, not content


class ToolError(Exception):
    """A failure the model should relay: the message says what to do next."""


def run(main) -> None:
    try:
        main(json.load(sys.stdin))
    except ToolError as e:
        sys.exit(str(e))


def workspace() -> Path:
    ws = os.environ.get("TANKA_WORKSPACE")
    if not ws:
        raise ToolError("TANKA_WORKSPACE is not set: this tool only runs from Tanka. Tell the user.")
    return Path(ws)


# ---------------------------------------------------------------- scope

def registry() -> dict:
    if not REGISTRY.is_file():
        raise ToolError(f"{REGISTRY} does not exist: the user has not assigned any mailbox to a workspace yet. Tell the user.")
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ToolError(f"{REGISTRY} is not valid JSON (line {e.lineno}). Tell the user; mail cannot be read until they fix it.")
    return {k.lower(): v for k, v in data.get("accounts", {}).items()}


def allowed(scope: str) -> list[str]:
    return sorted(addr for addr, a in registry().items() if a.get("workspace") == scope)


def pick_account(scope: str, account: str | None) -> str:
    """The one address this call may read, or a ToolError that says which ones exist."""
    mine = allowed(scope)
    if not mine:
        raise ToolError("No mailbox is assigned to this assistant. Tell the user: only they can assign one, in their account list.")
    if not account:
        if len(mine) == 1:
            return mine[0]
        raise ToolError(f"This assistant can read {len(mine)} mailboxes: {', '.join(mine)}. Ask the user which one.")
    want = account.strip().lower()
    hits = [a for a in mine if a == want or a.split("@")[0] == want]
    if len(hits) != 1:
        raise ToolError(f"\"{account}\" is not a mailbox this assistant may read. It may read: {', '.join(mine)}.")
    return hits[0]


# ---------------------------------------------------------------- session

# The browsers the headless wrapper (modules/common/chromium-headless) looks for, in its order.
BROWSERS = ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome-stable", "/usr/bin/google-chrome",
            "/usr/bin/brave", "/usr/bin/microsoft-edge", "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def real_browser() -> str:
    """The Chromium-based browser the session runs on: TANKA_BROWSER, else the first one installed."""
    for b in (os.environ.get("TANKA_BROWSER"), os.environ.get("TANKA_WHATSAPP_BROWSER"), *BROWSERS):
        if b and os.access(b, os.X_OK):
            return b
    raise ToolError("No Chromium, Chrome, Brave or Edge found. Install one, or set TANKA_BROWSER to its executable.")


def profile_dir() -> Path:
    """The browser profile Rastro keeps for the Gmail session (its sign-in lives here)."""
    if os.environ.get("TANKA_GMAIL_PROFILE"):
        return Path(os.environ["TANKA_GMAIL_PROFILE"])
    home = Path(os.environ.get("RASTRO_HOME") or Path.home() / ".local" / "share" / "rastro")
    return home / "profiles" / SESSION


def rastro(*args: str, timeout: int = 60) -> str:
    # The daemon reads RASTRO_CHROMIUM when it starts the browser; later calls ignore it.
    env = {**os.environ, "RASTRO_CHROMIUM": str(WRAPPER)}
    try:
        p = subprocess.run(["rastro", "-s", SESSION, *args], capture_output=True, text=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        raise ToolError(f"Rastro did not answer within {timeout} s. Tell the user; do not repeat the call.")
    if p.returncode != 0:
        raise ToolError(f"Rastro failed ({args[0]}): {(p.stderr or p.stdout).strip()[-300:]}. Tell the user.")
    return p.stdout


def js(expr: str, timeout: int = 60):
    out = rastro("eval", expr, timeout=timeout).strip().splitlines()
    if not out:
        raise ToolError("Gmail returned nothing. Tell the user.")
    value = json.loads(out[-1])
    return json.loads(value) if isinstance(value, str) else value


def page(body: str, **params) -> str:
    decl = "".join(f"  const {k} = {json.dumps(v, ensure_ascii=False)};\n" for k, v in params.items())
    return "(async () => {\n" + decl + body + "\n})()"


def running() -> bool:
    return bool(re.search(rf"^{re.escape(SESSION)}: running", rastro("status", timeout=20), re.M))


def current_url() -> str:
    tabs = json.loads(rastro("--json", "tabs", timeout=30) or "[]")
    active = [t for t in tabs if t.get("active")] or tabs
    return active[0]["url"] if active else ""


def goto(url: str) -> None:
    rastro("goto", url, timeout=90)


def upload_dirs() -> list[str]:
    ws = os.environ.get("TANKA_WORKSPACE")
    return ([ws] if ws else []) + UPLOAD_DIRS


@contextlib.contextmanager
def session_for_cli():
    """The session without a workspace: `tanka gmail status` reads the account list only."""
    with session():
        yield


@contextlib.contextmanager
def session():
    """One assistant at a time drives the shared Gmail tab."""
    HOME.mkdir(parents=True, exist_ok=True)
    LOCK.touch()
    with LOCK.open() as fh:
        deadline = time.time() + 60
        while True:
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > deadline:
                    raise ToolError("Another assistant is using Gmail. Tell the user to try again in a minute.")
                time.sleep(1)
        # Gmail searches and sends through POSTs to its own host and nowhere else. `open` on a
        # running session only replaces these permissions, so every call states them in full.
        perms = ["--allow-write", "mail.google.com"] + (["--allow-upload", ",".join(upload_dirs())] if upload_dirs() else [])
        if running():
            rastro("open", *perms, timeout=120)
        else:
            rastro("open", f"{BASE}/mail/u/0/", *perms, timeout=120)
        if not current_url().startswith(BASE + "/mail/"):
            goto(f"{BASE}/mail/u/0/")
        yield


ACCOUNTS = r"""
  // Each signed-in account answers at /mail/u/<n>/; past the last one Gmail serves account 0 again.
  const seen = [];
  for (let i = 0; i < max; i++) {
    const r = await fetch(`/mail/u/${i}/feed/atom`, {credentials: "include"});
    if (!r.ok) { if (i === 0) return JSON.stringify({error: r.status}); break; }
    const t = await r.text();
    const m = t.match(/<title>[^<]*?([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+)<\/title>/);
    if (!m || seen.some(s => s.address === m[1].toLowerCase())) break;
    seen.push({index: i, address: m[1].toLowerCase(), unread: Number((t.match(/<fullcount>(\d+)</) || [])[1] || 0)});
  }
  return JSON.stringify({accounts: seen});
"""


def signed_in() -> list[dict]:
    """Every account signed in to the session: [{index, address, unread}]. Needs session()."""
    res = js(page(ACCOUNTS, max=MAX_ACCOUNTS), timeout=60)
    if res.get("error") or not res.get("accounts"):
        raise ToolError("The Gmail session is not signed in to Google. Tell the user to run `tanka gmail login`; do not retry.")
    return res["accounts"]


def account_index(address: str) -> int:
    for a in signed_in():
        if a["address"] == address:
            return a["index"]
    raise ToolError(f"{address} is assigned to this assistant but is not signed in to the Gmail session. "
                    "Tell the user to add it with `tanka gmail login`.")


# ---------------------------------------------------------------- listing

LIST = r"""
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  // The list re-renders after the hash changes; wait until rows belong to this view.
  let rows = [];
  for (let i = 0; i < 40; i++) {
    await sleep(500);
    // Gmail keeps earlier views hidden in the page; only the visible rows belong to this one.
    rows = [...document.querySelectorAll("tr.zA")].filter(r => r.offsetParent !== null);
    const main = document.querySelector('div[role="main"]');
    const empty = main && /No hay|No messages|No se encontraron|didn't match|no coinciden/i.test(main.innerText.slice(0, 400));
    if ((rows.length || empty) && i >= 2) break;
  }
  const pick = (r, s) => r.querySelector(s);
  return JSON.stringify({ik: window.GLOBALS ? GLOBALS[9] : null, rows: rows.slice(0, limit).map(r => {
    const who = pick(r, "span[email]");
    const date = pick(r, "td.xW span[title], td.xW span");
    const idEl = pick(r, "[data-legacy-last-message-id]");
    const th = pick(r, "[data-legacy-thread-id]");
    return {id: idEl ? idEl.getAttribute("data-legacy-last-message-id") : null,
      thread: th ? th.getAttribute("data-legacy-thread-id") : null,
      from: who ? (who.getAttribute("name") || who.textContent.trim()) : "", email: who ? who.getAttribute("email") : "",
      subject: (pick(r, ".bog") || {}).textContent || "", snippet: ((pick(r, ".y2") || {}).textContent || "").replace(/\s+/g, " ").replace(/^\s*-\s*/, ""),
      date: date ? (date.getAttribute("title") || date.textContent) : "", unread: r.classList.contains("zE"),
      attachments: !!pick(r, ".brd, .aKS, [aria-label*='ttach'], [aria-label*='djunto']")};
  })});
"""


def listing_file(scope: str) -> Path:
    return HOME / f"listing-{re.sub(r'[^A-Za-z0-9_-]', '_', scope)}.json"


def list_messages(scope: str, account: str | None, search: str, unread: bool, limit: int) -> None:
    address = pick_account(scope, account)
    query = " ".join(x for x in (search.strip(), "is:unread" if unread else "") if x)
    hash_ = "search/" + urllib.parse.quote(query).replace("%20", "+") if query else "inbox"
    with session():
        index = account_index(address)
        # A full load: changing only the hash inside a running Gmail page does not always run the search.
        goto(f"{BASE}/mail/u/{index}/#{hash_}")
        res = js(page(LIST, limit=limit), timeout=90)
    rows = [r for r in res["rows"] if r["id"]]
    listing_file(scope).write_text(json.dumps({"address": address, "index": index, "view": hash_,
                                               "ids": {str(i): r["id"] for i, r in enumerate(rows, 1)},
                                               "threads": {str(i): r["thread"] or r["id"] for i, r in enumerate(rows, 1)},
                                               "subjects": {str(i): r["subject"] for i, r in enumerate(rows, 1)}}),
                                    encoding="utf-8")
    what = f"matching \"{query}\"" if query else "in the inbox"
    print(f"Today is {datetime.now():%Y-%m-%d}. {len(rows)} conversation(s) {what} for {address}, newest first "
          "(number | date | from | subject | marks | start):")
    for i, r in enumerate(rows, 1):
        marks = " ".join(x for x in ("UNREAD" if r["unread"] else "", "ATTACHMENTS" if r["attachments"] else "") if x) or "-"
        sender = f"{r['from']} <{r['email']}>" if r["email"] else r["from"]
        print(f"{i} | {r['date']} | {sender} | {r['subject'] or '(no subject)'} | {marks} | {r['snippet'][:90]}")
    if rows:
        print("To read one in full, with its attachments: gmail_read with its number. Nothing was marked as read.")


# ---------------------------------------------------------------- reading

RAW = r"""
  if (!location.pathname.startsWith(`/mail/u/${index}/`)) { location.href = `/mail/u/${index}/`; }
  for (let i = 0; i < 40 && !(window.GLOBALS && location.pathname.startsWith(`/mail/u/${index}/`)); i++)
    await new Promise(r => setTimeout(r, 500));
  const id = BigInt("0x" + hex).toString();
  // "Show original" leaves attachment bodies out; its "Download original" link has the whole message.
  const om = await fetch(`/mail/u/${index}/?ik=${GLOBALS[9]}&view=om&permmsgid=msg-f:${id}`, {credentials: "include"});
  if (!om.ok) return JSON.stringify({error: om.status});
  const link = ((await om.text()).match(/href="([^"]*view=att[^"]*disp=comp[^"]*)"/) || [])[1];
  if (!link) return JSON.stringify({error: "no download link"});
  const r = await fetch(new URL(link.replace(/&amp;/g, "&"), location.href), {credentials: "include"});
  if (!r.ok) return JSON.stringify({error: r.status});
  const t = await r.text();
  if (t.length > max) return JSON.stringify({error: "size", size: t.length});
  return JSON.stringify({raw: t});
"""


def to_text(part) -> str:
    body = part.get_content()
    if part.get_content_type() == "text/html":
        body = re.sub(r"(?is)<(script|style|head)[^>]*>.*?</\1>", "", body)
        body = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</li>", "\n", body)
        body = re.sub(r"(?i)</td>", " | ", body)
        body = html.unescape(re.sub(r"<[^>]+>", "", body))
    body = re.sub(r"[ \t\xa0]+", " ", body)
    return re.sub(r"\n\s*\n+", "\n\n", body).strip()


def safe(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "-", (name or "attachment").strip()).strip("-")[:100] or "attachment"


def parse(raw: str, folder: Path) -> dict:
    """Headers, text body and saved attachments of one MIME message."""
    msg = email.message_from_string(raw.lstrip(), policy=email.policy.default)
    body_part = msg.get_body(preferencelist=("plain", "html"))
    saved, skipped = [], []
    for part in msg.iter_attachments():
        name = part.get_filename() or ""
        inline = part.get_content_disposition() == "inline"
        data = part.get_payload(decode=True) or b""
        if inline and len(data) < TINY_INLINE:
            continue
        if not name:
            name = "image" + (("." + part.get_content_subtype()) if part.get_content_maintype() == "image" else ".bin")
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / safe(name)
        path.write_bytes(data)
        note = ""
        if path.suffix.lower() in (".xlsx", ".docx"):
            text = office.office_text(path)
            if text:
                txt = path.with_name(path.name + ".txt")
                txt.write_text(text, encoding="utf-8")
                note = f"; its content as text is in {txt}"
        saved.append(f"{path}{' [picture inside the message]' if inline else ''}{note}")
    try:
        when = parsedate_to_datetime(msg["date"]).astimezone().strftime("%Y-%m-%d %H:%M") if msg["date"] else "?"
    except (TypeError, ValueError):
        when = str(msg["date"])
    return {"from": str(msg["from"] or ""), "to": str(msg["to"] or ""), "cc": str(msg["cc"] or ""),
            "subject": str(msg["subject"] or "(no subject)"), "date": when,
            "body": to_text(body_part) if body_part else "", "saved": saved, "skipped": skipped}


def from_listing(scope: str, number: int) -> tuple[dict, str]:
    """The last listing and the address it belongs to, if `number` is in it and the address is still ours."""
    f = listing_file(scope)
    listing = json.loads(f.read_text(encoding="utf-8")) if f.is_file() else {}
    if str(number) not in listing.get("ids", {}):
        raise ToolError(f"There is no message number {number} in the last list. Call gmail_inbox first.")
    if listing["address"] not in allowed(scope):
        raise ToolError(f"{listing['address']} is no longer assigned to this assistant. Tell the user.")
    return listing, listing["address"]


def read_message(scope: str, number: int) -> None:
    listing, address = from_listing(scope, number)
    hex_id = listing["ids"][str(number)]
    with session():
        index = account_index(address)
        res = js(page(RAW, index=index, hex=hex_id, max=MAX_BYTES), timeout=150)
    if res.get("error") == "size":
        raise ToolError(f"That message is {res['size'] // (1024 * 1024)} MB, over the {MAX_BYTES // (1024 * 1024)} MB limit. "
                        "Tell the user to open it in Gmail.")
    if res.get("error"):
        raise ToolError(f"Gmail answered {res['error']} for that message. Tell the user; do not retry.")
    stamp = datetime.now().strftime("%Y%m%d")
    m = parse(res["raw"], workspace() / ATTACH_DIR / address.split("@")[0] / f"{stamp}-{hex_id}")
    body = m["body"]
    print(f"Message {number} of {address}: \"{m['subject']}\", {len(m['saved'])} attachment(s) saved. Not marked as read.")
    print(f"From: {m['from']}")
    print(f"To: {m['to']}")
    if m["cc"]:
        print(f"Cc: {m['cc']}")
    print(f"Date: {m['date']}")
    print("---")
    print(body[:BODY_CAP] + ("\n… (body cut)" if len(body) > BODY_CAP else ""))
    print("---")
    for s in m["saved"]:
        print(f"Attachment saved: {s}")
    if m["saved"]:
        print("To look at an image or a PDF, open it with Read at that exact path.")


# ---------------------------------------------------------------- sending

EMAIL_RE = re.compile(r"[^@\s,;<>]+@[^@\s,;<>]+\.[A-Za-z]{2,}")

OPEN_THREAD = r"""
  // Gmail opens a conversation from its list row; a thread id typed in the URL does not always route.
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 20; i++) {
    const el = [...document.querySelectorAll(`[data-legacy-thread-id="${thread}"]`)].find(e => e.offsetParent !== null);
    if (el) {
      const row = el.closest("tr"), r = row.getBoundingClientRect();
      const o = {bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0};
      for (const t of ["mousedown", "mouseup", "click"]) row.dispatchEvent(new MouseEvent(t, o));
      for (let j = 0; j < 20; j++) { await sleep(500); if (document.querySelector("h2.hP")) return JSON.stringify({ok: true}); }
      return JSON.stringify({error: "the conversation did not open"});
    }
    await sleep(500);
  }
  return JSON.stringify({missing: true});
"""

OPEN_REPLY = r"""
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const label = b => (b.getAttribute("data-tooltip") || b.getAttribute("aria-label") || "").trim();
  for (let i = 0; i < 40; i++) {
    // Reply on the last message of the thread; the label is localised, the class is not.
    const btns = [...document.querySelectorAll('div[role="button"], span[role="link"]')]
      .filter(b => b.offsetParent !== null && (/^(Reply|Responder)$/i.test(label(b)) || b.classList.contains("bkH")));
    if (btns.length) { btns[btns.length - 1].click(); break; }
    await sleep(500);
  }
  for (let i = 0; i < 30; i++) {
    const body = [...document.querySelectorAll('div[contenteditable="true"][role="textbox"]')].filter(b => b.offsetParent !== null).pop();
    if (body) return JSON.stringify({ok: true});
    await sleep(500);
  }
  return JSON.stringify({error: "no reply box"});
"""

WRITE_BODY = r"""
  const body = [...document.querySelectorAll('div[contenteditable="true"][role="textbox"]')].filter(b => b.offsetParent !== null).pop();
  if (!body) return JSON.stringify({error: "no message box"});
  body.focus();
  // insertText keeps Gmail's editor state in step; setting innerHTML would be sent as an empty message.
  const r = document.createRange(); r.selectNodeContents(body); r.collapse(true);
  const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
  document.execCommand("insertText", false, text);
  return JSON.stringify({ok: body.innerText.includes(text.slice(0, 40).trim())});
"""

EXPOSE_UPLOAD = r"""
  // Gmail's file input is hidden and out of the accessibility tree, where Rastro finds what it can
  // act on. Show it for one upload so Rastro, which enforces the upload allowlist, can fill it.
  const f = [...document.querySelectorAll('input[type="file"][name="Filedata"]')].pop();
  if (!f) return JSON.stringify({error: "no file input"});
  f.setAttribute("aria-label", "tanka-upload");
  f.removeAttribute("aria-hidden");
  f.tabIndex = 0;
  f.style.cssText = "display:block!important;position:fixed;top:0;left:0;width:200px;height:40px;opacity:1;z-index:2147483647;visibility:visible";
  for (let p = f.parentElement; p; p = p.parentElement) {
    if (p.hasAttribute && p.hasAttribute("aria-hidden")) p.removeAttribute("aria-hidden");
    if (p.style && getComputedStyle(p).display === "none") p.style.display = "block";
  }
  return JSON.stringify({ok: true});
"""

ATTACHED = r"""
  for (let i = 0; i < 120; i++) {
    const chip = [...document.querySelectorAll("div, a, span")].find(e => e.children.length < 6 && e.textContent.includes(name)
      && e.offsetParent !== null && !e.closest('[contenteditable="true"]'));
    const busy = [...document.querySelectorAll('[role="progressbar"]')].some(e => e.offsetParent !== null);
    if (chip && !busy) {
      const f = document.querySelector('input[aria-label="tanka-upload"]');
      if (f) { f.removeAttribute("aria-label"); f.style.cssText = "display:none"; }
      return JSON.stringify({ok: true});
    }
    await new Promise(r => setTimeout(r, 500));
  }
  return JSON.stringify({error: "the attachment did not finish uploading"});
"""

SEND = r"""
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const label = b => (b.getAttribute("data-tooltip") || b.getAttribute("aria-label") || b.textContent || "").trim();
  const send = [...document.querySelectorAll('div[role="button"]')]
    .filter(b => b.offsetParent !== null && (/^(Send|Enviar)\b/i.test(label(b)) || b.classList.contains("aoO"))).pop();
  if (!send) return JSON.stringify({error: "no send button"});
  send.click();
  for (let i = 0; i < 60; i++) {
    await sleep(500);
    const toast = [...document.querySelectorAll('[role="alert"], span.bAq, div.vh')].map(e => e.textContent).join(" ");
    if (/Message sent|Mensaje enviado|Enviado|Sent/i.test(toast)) return JSON.stringify({ok: true});
    if (/couldn.t|no se pudo|error/i.test(toast)) return JSON.stringify({error: toast.slice(0, 160)});
  }
  return JSON.stringify({unconfirmed: true});
"""


def check_unattended(address: str) -> None:
    """With nobody watching, only mailboxes the user set to auto_reply may send."""
    if os.environ.get("TANKA_UNATTENDED") and not registry().get(address, {}).get("auto_reply", False):
        raise ToolError(f"The user is not here and {address} is not set to auto_reply. Nothing was sent: "
                        "save the message you propose in .tanka/drafts/ and report it for the user to approve.")


def check_attachment(attachment: str | None) -> Path | None:
    if not attachment:
        return None
    path = Path(attachment).expanduser().resolve()
    roots = [Path(d).expanduser().resolve() for d in upload_dirs()]
    if not any(path == r or r in path.parents for r in roots):
        raise ToolError(f"{attachment} is outside the folders attachments may come from ({', '.join(map(str, roots))}). "
                        "Tell the user; nothing was sent.")
    if not path.is_file():
        raise ToolError(f"{attachment} does not exist. Ask the user for the right file; nothing was sent.")
    if path.stat().st_size > MAX_BYTES:
        raise ToolError(f"{path.name} is over the {MAX_BYTES // (1024 * 1024)} MB limit. Tell the user; nothing was sent.")
    return path


def step(expr: str, what: str, timeout: int = 90) -> dict:
    res = js(expr, timeout=timeout)
    if res.get("error"):
        raise ToolError(f"Gmail did not {what} ({res['error']}). Tell the user; nothing was sent. Do not retry.")
    return res


def attach(path: Path) -> None:
    step(page(EXPOSE_UPLOAD), "offer a place for the attachment")
    found = re.search(r"\[(\w+)\] button «tanka-upload»", rastro("view", "--find", "tanka-upload", timeout=30))
    if not found:
        raise ToolError("The attachment could not be added. Tell the user; nothing was sent. Do not retry.")
    rastro("act", found.group(1), "upload", str(path), timeout=120)
    step(page(ATTACHED, name=path.name), "finish uploading the attachment", timeout=90)


SENT_CHECK = r"""
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 20; i++) {
    await sleep(500);
    const rows = [...document.querySelectorAll("tr.zA")].filter(r => r.offsetParent !== null);
    if (rows.length) return JSON.stringify({count: rows.length});
    const main = document.querySelector('div[role="main"]');
    if (main && /No hay|No messages|No se encontraron|didn't match|no coinciden/i.test(main.innerText.slice(0, 400))) return JSON.stringify({count: 0});
  }
  return JSON.stringify({count: 0});
"""


def sent_recently(index: int, to: str, subject: str) -> bool:
    q = f'in:sent to:{to} subject:"{subject}" newer_than:1h'
    goto(f"{BASE}/mail/u/{index}/#search/" + urllib.parse.quote(q).replace("%20", "+"))
    return js(page(SENT_CHECK), timeout=60)["count"] > 0


def finish(index: int, to: str, subject: str, what: str) -> None:
    res = step(page(SEND), "send it", timeout=60)
    if res.get("unconfirmed") and not sent_recently(index, to, subject):
        raise ToolError(f"Gmail did not confirm the {what}: it may or may not have gone out. "
                        "Ask the user to check Sent in Gmail; do not retry.")
    print(f"{what.capitalize()} sent to {to}, subject \"{subject}\". Do not repeat it.")


def send_mail(scope: str, account: str | None, to: str, subject: str, message: str, attachment: str | None) -> None:
    address = pick_account(scope, account)
    check_unattended(address)
    recipients = EMAIL_RE.findall(to)
    if not recipients or len(recipients) > 10:
        raise ToolError(f"\"{to}\" has no valid email address (or more than 10). Ask the user; nothing was sent.")
    if not message.strip() or not subject.strip():
        raise ToolError("The subject or the message is empty. Ask the user; nothing was sent.")
    path = check_attachment(attachment)
    with session():
        index = account_index(address)
        if sent_recently(index, recipients[0], subject):
            print(f"A message with subject \"{subject}\" already went to {recipients[0]} in the last hour; it was not sent again.")
            return
        params = urllib.parse.urlencode({"view": "cm", "fs": "1", "tf": "1", "to": ",".join(recipients), "su": subject})
        goto(f"{BASE}/mail/u/{index}/?{params}")
        step(page(WRITE_BODY, text=message), "take the message text")
        if path:
            attach(path)
        finish(index, recipients[0], subject, "message")


def reply(scope: str, number: int, message: str, attachment: str | None) -> None:
    listing, address = from_listing(scope, number)
    check_unattended(address)
    if not message.strip():
        raise ToolError("The message is empty. Ask the user what to reply; nothing was sent.")
    path = check_attachment(attachment)
    thread = listing["threads"][str(number)]
    subject = listing.get("subjects", {}).get(str(number), "")
    with session():
        index = account_index(address)
        goto(f"{BASE}/mail/u/{index}/#{listing.get('view', 'inbox')}")
        if step(page(OPEN_THREAD, thread=thread), "open the conversation").get("missing") and subject:
            # The list moved since it was read: find the conversation by its subject instead.
            goto(f"{BASE}/mail/u/{index}/#search/" + urllib.parse.quote(f'subject:"{subject}"').replace("%20", "+"))
            if step(page(OPEN_THREAD, thread=thread), "open the conversation").get("missing"):
                raise ToolError("That conversation is no longer in the list. Call gmail_inbox again; nothing was sent.")
        step(page(OPEN_REPLY), "open the reply box")
        step(page(WRITE_BODY, text=message), "take the reply text")
        if path:
            attach(path)
        res = step(page(SEND), "send the reply", timeout=60)
    if res.get("unconfirmed"):
        raise ToolError("Gmail did not confirm the reply: it may or may not have gone out. "
                        "Ask the user to check the conversation in Gmail; do not retry.")
    print(f"Reply sent from {address} in the conversation \"{subject}\"{' with ' + path.name if path else ''}. Do not repeat it.")


# ---------------------------------------------------------------- archiving and the trash

ACT = r"""
  // The open conversation's toolbar: act 7 archives, act 10 moves to the trash. Done when the list is back.
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const b = [...document.querySelectorAll(`[act="${act}"]`)].find(e => e.getBoundingClientRect().width > 0);
  if (!b) return JSON.stringify({error: "no button for it"});
  const r = b.getBoundingClientRect();
  const o = {bubbles: true, cancelable: true, view: window, clientX: r.left + r.width / 2, clientY: r.top + r.height / 2, button: 0};
  for (const t of ["mousedown", "mouseup", "click"]) b.dispatchEvent(new MouseEvent(t, o));
  for (let i = 0; i < 20; i++) { await sleep(500); if (!document.querySelector("h2.hP")) return JSON.stringify({ok: true}); }
  return JSON.stringify({unconfirmed: true});
"""

IN_VIEW = r"""
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 20; i++) {
    await sleep(500);
    const rows = [...document.querySelectorAll("tr.zA")].filter(r => r.offsetParent !== null);
    const main = document.querySelector('div[role="main"]');
    const empty = main && /No hay|No messages|No se encontraron|didn't match|no coinciden/i.test(main.innerText.slice(0, 400));
    if (rows.length || empty) return JSON.stringify({found: rows.some(r => r.querySelector(`[data-legacy-thread-id="${thread}"]`))});
  }
  return JSON.stringify({found: null});
"""


def _act_on(listing: dict, number: int, act: int, what: str) -> str:
    """Open the conversation from its list, press one toolbar button, and say whether it left the inbox."""
    thread = listing["threads"][str(number)]
    subject = listing.get("subjects", {}).get(str(number), "")
    index = account_index(listing["address"])
    goto(f"{BASE}/mail/u/{index}/#{listing.get('view', 'inbox')}")
    if step(page(OPEN_THREAD, thread=thread), "open the conversation").get("missing"):
        raise ToolError(f"That conversation is no longer in the list. Call gmail_inbox again; nothing was {what}.")
    res = step(page(ACT, act=act), f"{what} it", timeout=60)
    goto(f"{BASE}/mail/u/{index}/#search/" + urllib.parse.quote(f'in:inbox subject:"{subject}"').replace("%20", "+"))
    still = js(page(IN_VIEW, thread=thread), timeout=60).get("found")
    if res.get("unconfirmed") or still:
        raise ToolError(f"Gmail did not confirm it: the conversation may still be in the inbox. Ask the user to check; do not retry.")
    return subject


def archive(scope: str, number: int) -> None:
    listing, address = from_listing(scope, number)
    with session():
        subject = _act_on(listing, number, 7, "archived")
    print(f"Archived \"{subject}\" in {address}: it left the inbox and is still in All Mail. Do not repeat it.")


REQUESTS = HOME / "requests"
SCOPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def requests_file(scope: str) -> Path:
    if not SCOPE_RE.fullmatch(scope or ""):
        raise ToolError(f"'{scope}' is not a workspace scope. Tell the user.")
    return REQUESTS / f"{scope}.json"


@contextlib.contextmanager
def requests_locked(scope: str):
    f = requests_file(scope)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f.with_suffix(".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_requests(scope: str) -> list[dict]:
    try:
        data = json.loads(requests_file(scope).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("requests") if isinstance(data, dict) else None
    return [r for r in items if isinstance(r, dict) and r.get("id")] if isinstance(items, list) else []


def write_requests(scope: str, items: list[dict]) -> None:
    f = requests_file(scope)
    f.parent.mkdir(parents=True, exist_ok=True)
    pending = [r for r in items if r.get("status") == "pending"]
    rest = [r for r in items if r.get("status") != "pending"][:30]
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"requests": pending + rest}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(f)


def trash_tool(scope: str, number: int) -> None:
    """Leave a request for the user to confirm on the page. Nothing is moved here, ever."""
    listing, address = from_listing(scope, number)
    thread = listing["threads"][str(number)]
    with requests_locked(scope):
        items = read_requests(scope)
        if any(r["thread"] == thread and r.get("status") == "pending" for r in items):
            raise ToolError("A request to move that conversation to the trash is already waiting for the user.")
        r = {"id": "g-" + secrets.token_hex(3), "address": address, "thread": thread, "view": listing.get("view", "inbox"),
             "index": listing.get("index"), "subject": listing.get("subjects", {}).get(str(number), ""),
             "t": round(time.time(), 3), "status": "pending"}
        write_requests(scope, [r] + items)
    print(f"Request {r['id']} saved to move \"{r['subject']}\" to the trash. NOTHING was moved: the user must press "
          "Delete in the chat. Tell them, and do not say it is in the trash until they confirm.")


def _pending(items: list[dict], rid: str) -> dict:
    r = next((x for x in items if x["id"] == rid), None)
    if r is None or r.get("status") != "pending":
        raise ToolError(f"No pending request {rid} in this workspace.")
    return r


def confirm_trash(scope: str, rid: str) -> str:
    """The user's click: move the conversation to the trash (Gmail keeps it there 30 days)."""
    with requests_locked(scope):
        items = read_requests(scope)
        r = _pending(items, rid)
        if r["address"] not in allowed(scope):
            raise ToolError(f"{r['address']} is no longer assigned to this assistant.")
        listing = {"address": r["address"], "view": r["view"], "threads": {"1": r["thread"]}, "subjects": {"1": r["subject"]}}
        with session():
            _act_on(listing, 1, 10, "moved to the trash")
        r["status"], r["closed"] = "deleted", round(time.time(), 3)
        write_requests(scope, items)
    return f"Moved \"{r['subject']}\" to the trash. Gmail keeps it there for 30 days."


def dismiss_trash(scope: str, rid: str) -> str:
    with requests_locked(scope):
        items = read_requests(scope)
        r = _pending(items, rid)
        r["status"], r["closed"] = "dismissed", round(time.time(), 3)
        write_requests(scope, items)
    return f"Kept \"{r['subject']}\"; nothing was moved."


# ---------------------------------------------------------------- events (for triggers)

FEED = r"""
  const out = [];
  for (const a of accounts) {
    const r = await fetch(`/mail/u/${a.index}/feed/atom`, {credentials: "include"});
    if (!r.ok) continue;
    const t = await r.text();
    for (const e of t.split("<entry>").slice(1)) {
      const id = (e.match(/<id>([^<]+)<\/id>/) || [])[1];
      const from = (e.match(/<author>[\s\S]*?<email>([^<]+)<\/email>/) || [])[1] || "";
      if (id) out.push({account: a.address, id, from});
    }
  }
  return JSON.stringify({entries: out});
"""


def drain_events() -> list[dict]:
    """Unread mail that arrived since the last call, in mailboxes assigned to some workspace. No subject or text.

    The first call per mailbox only remembers what is already unread, so a trigger
    does not fire once for every old message.
    """
    reg = registry()
    state_file = HOME / "events-state.json"
    state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.is_file() else {}
    with session():
        accounts = [a for a in signed_in() if a["address"] in reg]
        res = js(page(FEED, accounts=accounts), timeout=90)
    events, seen = [], {}
    for e in res["entries"]:
        seen.setdefault(e["account"], []).append(e["id"])
        if e["account"] in state and e["id"] not in state[e["account"]]:
            events.append({"source": "gmail", "kind": "mail", "scope": reg[e["account"]].get("workspace"),
                           "account": e["account"], "from": e["from"], "id": e["id"]})
    for account, ids in seen.items():
        state[account] = ids
    for a in accounts:
        state.setdefault(a["address"], [])
    state_file.write_text(json.dumps(state), encoding="utf-8")
    return events
