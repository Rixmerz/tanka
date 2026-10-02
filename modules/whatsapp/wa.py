"""WhatsApp for Tanka: the shared library behind every workspace's WhatsApp tools.

One linked WhatsApp Web session (Rastro session `whatsapp`, headless) serves
every assistant. What each assistant may see is decided here, never by the model:

- `contacts.json` (in TANKA_WHATSAPP_HOME) maps a phone number to a role, and
  each role to the one workspace allowed to see it and to whether it may be
  read and replied to. The user edits it; the assistants cannot, because they
  only write inside their own workspace.
- A number that is not in the file, or whose role belongs to another workspace,
  is never read and never written to. Groups are never read.

Messages are read from WhatsApp Web's in-memory collections, which does not
send read receipts: the user's unread badges stay as they were.

What the assistant knows about a person lives in the workspace, in
`notes/people/<number>.md` (TANKA_WHATSAPP_PEOPLE_DIR): a `key: value` header (any fields, e.g.
sale_status) and free notes below. The assistant writes those; the role never.
"""
import contextlib
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import media  # noqa: E402  (same folder)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "common"))
import office  # noqa: E402  (modules/common)

MODULE = Path(__file__).resolve().parent
# Every setting has a default and an environment variable; see README.md.
# User data lives outside every workspace, so no assistant can write it.
HOME = Path(os.environ.get("TANKA_WHATSAPP_HOME", Path.home() / ".tanka" / "shared" / "whatsapp"))
REGISTRY = HOME / "contacts.json"
LOCK = HOME / ".lock"
SESSION = os.environ.get("TANKA_WHATSAPP_SESSION", "whatsapp")
# Relative to the workspace: the assistant's record of each person, and downloaded media.
PEOPLE_DIR = os.environ.get("TANKA_WHATSAPP_PEOPLE_DIR", "notes/people")
MEDIA_DIR = os.environ.get("TANKA_WHATSAPP_MEDIA_DIR", "whatsapp")
LIST_LIMIT = int(os.environ.get("TANKA_WHATSAPP_LIST_LIMIT", "25"))
URL = "https://web.whatsapp.com/"
WRAPPER = MODULE.parent / "common" / "chromium-headless"


class ToolError(Exception):
    """A failure the model should relay: the message says what to do next."""


def digits(s) -> str:
    return re.sub(r"\D", "", str(s or ""))


def registry() -> dict:
    if not REGISTRY.is_file():
        raise ToolError(f"{REGISTRY} does not exist: the user has not assigned roles to any contact yet. Tell the user.")
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ToolError(f"{REGISTRY} is not valid JSON (line {e.lineno}). Tell the user; WhatsApp cannot be read until they fix it.")
    return {"roles": data.get("roles", {}), "contacts": {digits(k): v for k, v in data.get("contacts", {}).items()}}


def registry_lock_path() -> Path:
    """The lock that serialises writes to contacts.json. Not LOCK, which is held for a whole browser call."""
    return REGISTRY.with_name(".contacts.lock")


def registry_raw() -> dict:
    """contacts.json exactly as stored (keys unnormalised, unknown keys kept), for a write to change and save."""
    if not REGISTRY.is_file():
        raise ToolError(f"{REGISTRY} does not exist yet.")
    try:
        data = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ToolError(f"{REGISTRY} is not valid JSON (line {e.lineno}); fix it by hand first.")
    if not isinstance(data, dict):
        raise ToolError(f"{REGISTRY} must hold a JSON object.")
    for key in ("roles", "contacts"):
        if not isinstance(data.setdefault(key, {}), dict):
            raise ToolError(f"\"{key}\" in {REGISTRY} must be an object.")
    return data


def registry_update(change):
    """Apply change(data) to contacts.json under an exclusive lock and save it atomically.

    `change` edits the stored dict in place, so every key it does not touch is kept, and returns what
    the caller gets back. If it raises, nothing is written. The automation daemon reads the file
    without the lock; the atomic replace means it sees the old file or the new one, never half of one.
    """
    REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    with registry_lock_path().open("a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        data = registry_raw()
        result = change(data)
        mode = REGISTRY.stat().st_mode & 0o777
        fd, tmp = tempfile.mkstemp(dir=REGISTRY.parent, prefix=".contacts.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(data, out, ensure_ascii=False, indent=2)
                out.write("\n")
                out.flush()
                os.fsync(out.fileno())
            os.chmod(tmp, mode)
            os.replace(tmp, REGISTRY)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise
    return result


def contact_keys(contacts: dict, num: str) -> list[str]:
    """The stored keys of one number, however each was typed ("+1 555 123 4567", "15551234567")."""
    return [k for k in contacts if digits(k) == num]


def scope(workspace_name: str) -> tuple[dict, dict]:
    """Roles that belong to this workspace, and the contacts holding one of them (keyed by digits)."""
    reg = registry()
    roles = {name: r for name, r in reg["roles"].items() if r.get("workspace") == workspace_name}
    people = {num: c for num, c in reg["contacts"].items() if c.get("role") in roles}
    return roles, people


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
        raise ToolError("WhatsApp Web returned nothing. Tell the user.")
    value = json.loads(out[-1])
    return json.loads(value) if isinstance(value, str) else value


READY = r"""(async () => {
  for (let i = 0; i < 180; i++) {
    try { if (window.require("WAWebCollections").Chat.getModelsArray().length) return JSON.stringify("ok"); } catch (e) {}
    if (document.querySelector("[data-ref]") || /Scan to log in|Escanea el código/.test(document.body.innerText)) return JSON.stringify("qr");
    if (/actualiza Chrome|update Chrome|Chrome 100/i.test(document.body.innerText)) return JSON.stringify("browser");
    await new Promise(r => setTimeout(r, 500));
  }
  return JSON.stringify("timeout");
})()"""


def running() -> bool:
    return bool(re.search(rf"^{re.escape(SESSION)}: running", rastro("status", timeout=20), re.M))


def open_session() -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    if not running():
        rastro("open", URL, "--allow-write", "web.whatsapp.com", timeout=120)


def ensure_ready() -> None:
    open_session()
    state = js(READY, timeout=120)
    if state == "qr":
        raise ToolError("WhatsApp is not linked on this computer. Tell the user to link it by running "
                        "`tanka whatsapp link` in a terminal; do not retry.")
    if state == "browser":
        raise ToolError("WhatsApp rejected the browser as unsupported. Tell the user; do not retry.")
    if state != "ok":
        raise ToolError("WhatsApp Web did not finish loading. Tell the user; do not retry.")


@contextlib.contextmanager
def session():
    """One assistant at a time drives the shared WhatsApp tab."""
    HOME.mkdir(parents=True, exist_ok=True)
    LOCK.touch()
    with LOCK.open() as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            deadline = time.time() + 60
            while True:
                time.sleep(1)
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.time() > deadline:
                        raise ToolError("Another assistant is using WhatsApp. Tell the user to try again in a minute.")
        ensure_ready()
        yield


# Page helpers shared by every expression below. `pn(chat)` is the phone number
# behind a chat: new chats are keyed by an internal "lid" id, not the number.
PRELUDE = r"""
  const C = window.require("WAWebCollections");
  const pn = c => { if (c.id.server === "c.us") return c.id.user;
    if (c.id.server !== "lid") return null;
    const ct = C.Contact.get(c.id); const p = ct && ct.phoneNumber; return p ? (p.user || String(p).split("@")[0]) : null; };
  const fmt = t => { const d = new Date(t * 1000), z = n => String(n).padStart(2, "0");
    return `${d.getFullYear()}-${z(d.getMonth() + 1)}-${z(d.getDate())} ${z(d.getHours())}:${z(d.getMinutes())}`; };
  const text = m => {
    if (m.type === "revoked") return "[deleted message]";
    const cap = (m.caption || "").trim();
    const kind = {image: "image", video: "video", audio: "audio", ptt: "voice note", sticker: "sticker",
      location: "location", vcard: "contact card", document: "document"}[m.type];
    if (kind) return `[${kind}${m.type === "document" && m.filename ? ": " + m.filename : ""}]${cap ? " " + cap : ""}`;
    return (m.body || "").replace(/\s+/g, " ").trim() || `[${m.type}]`;
  };
"""


def page(body: str, **params) -> str:
    """Wrap a page script body with the prelude and JSON-encoded params."""
    decl = "".join(f"  const {k} = {json.dumps(v, ensure_ascii=False)};\n" for k, v in params.items())
    return "(async () => {\n" + PRELUDE + decl + body + "\n})()"


def find_person(people: dict, contact: str) -> tuple[str, dict]:
    """Resolve a number or a name to one registered contact of this workspace, or fail saying why."""
    num = digits(contact)
    if len(num) >= 8:
        hits = [(n, c) for n, c in people.items() if n.endswith(num[-8:])]
    else:
        q = contact.strip().lower()
        hits = [(n, c) for n, c in people.items() if q and q in str(c.get("name", "")).lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        names = ", ".join(f"{c.get('name', '?')} ({n})" for n, c in hits[:6])
        raise ToolError(f"{len(hits)} contacts match \"{contact}\": {names}. Ask the user which one.")
    raise ToolError(f"\"{contact}\" is not a contact this assistant may read or reply to. "
                    "Tell the user: only they can give it a role in their contact list.")


def run(main) -> None:
    try:
        main(json.load(sys.stdin))
    except ToolError as e:
        sys.exit(str(e))


# ---------------------------------------------------------------- operations

LIST = r"""
  const rows = []; let hidden = 0, hiddenUnread = 0;
  for (const c of C.Chat.getModelsArray()) {
    if (c.id.server === "g.us" || c.id.server === "newsletter" || c.id.server === "broadcast") continue;
    const n = pn(c);
    if (!n || !allowed.includes(n)) { hidden++; if (c.unreadCount > 0) hiddenUnread++; continue; }
    const last = c.msgs.getModelsArray().slice(-1)[0];
    rows.push({n, unread: c.unreadCount || 0, t: c.t || (last && last.t) || 0,
      last: last ? {when: fmt(last.t), mine: !!last.id.fromMe, text: text(last)} : null});
  }
  rows.sort((a, b) => (b.unread > 0) - (a.unread > 0) || b.t - a.t);
  return JSON.stringify({rows, hidden, hiddenUnread});
"""


def list_chats(workspace_name: str, limit: int = LIST_LIMIT) -> None:
    roles, people = scope(workspace_name)
    readable = [n for n, c in people.items() if roles[c["role"]].get("read", False)]
    with session():
        res = js(page(LIST, allowed=readable), timeout=60)
    rows = res["rows"][:limit]
    unread = sum(1 for r in res["rows"] if r["unread"])
    shown = f" (showing {len(rows)})" if len(rows) < len(res["rows"]) else ""
    print(f"{len(res['rows'])} chat(s) with contacts you may read, {unread} with unread messages{shown}:")
    for r in rows:
        c = people[r["n"]]
        last = r["last"]
        who = "you" if last and last["mine"] else c.get("name", "?")
        tail = f"last {last['when']}, {who}: {last['text'][:70]}" if last else "no messages loaded"
        print(f"+{r['n']} | {c.get('name', '?')} | {c['role']} | {r['unread']} unread | {tail}")
    if res["hiddenUnread"]:
        print(f"Also {res['hiddenUnread']} chat(s) with unread messages from people without a role this assistant "
              "may read; they are not read. Only the user can give them a role.")


THREAD = r"""
  const chat = C.Chat.getModelsArray().find(c => pn(c) === number && c.id.server !== "g.us");
  if (!chat) return JSON.stringify({none: true});
  const L = window.require("WAWebChatLoadMessages");
  // WhatsApp changed this signature from (chat) to ({chat}) in 2026; try the new one first.
  const earlier = async () => { try { return await L.loadEarlierMsgs({chat}); } catch (e) { return await L.loadEarlierMsgs(chat); } };
  for (let i = 0; i < 4 && chat.msgs.getModelsArray().length < limit + 5; i++) {
    const before = chat.msgs.getModelsArray().length;
    try { await earlier(); } catch (e) { break; }
    if (chat.msgs.getModelsArray().length === before) break;
  }
  const msgs = chat.msgs.getModelsArray().filter(m => m.type !== "e2e_notification" && m.type !== "notification_template"
    && m.type !== "gp2" && m.type !== "protocol").slice(-limit);
  const MEDIA = ["image", "video", "audio", "ptt", "document", "sticker"];
  const z = n => String(n).padStart(2, "0");
  const stamp = t => { const d = new Date(t * 1000); return `${d.getFullYear()}${z(d.getMonth() + 1)}${z(d.getDate())}-${z(d.getHours())}${z(d.getMinutes())}`; };
  return JSON.stringify({unread: chat.unreadCount || 0,
    msgs: msgs.map(m => ({when: fmt(m.t), mine: !!m.id.fromMe, text: text(m),
      media: withMedia && MEDIA.includes(m.type) ? {type: m.type, id: m.id.id, stamp: stamp(m.t), mime: m.mimetype,
        filename: m.filename || null, directPath: m.directPath, mediaKey: m.mediaKey, filehash: m.filehash,
        size: m.size || 0, duration: Number(m.duration) || 0} : null}))});
"""


def workspace() -> Path:
    ws = os.environ.get("TANKA_WORKSPACE")
    if not ws:
        raise ToolError("TANKA_WORKSPACE is not set: this tool only runs from Tanka. Tell the user.")
    return Path(ws)


def person_record(num: str, ws: Path | None = None) -> tuple[dict, str]:
    """Fields and free text of <PEOPLE_DIR>/<num>.md, the assistant's own record of a person."""
    path = (ws or workspace()) / PEOPLE_DIR / f"{num}.md"
    if not path.is_file():
        return {}, ""
    text = path.read_text(encoding="utf-8")
    fields = {}
    m = re.match(r"---\n(.*?)\n---\n?", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                if k.strip() and v.strip():
                    fields[k.strip().lower()] = v.strip()
        text = text[m.end():]
    return fields, text.strip()


def describe_media(m: dict, folder: Path) -> str:
    """One line saying what the attachment is and where it is."""
    info = m["media"]
    try:
        path = media.save(info, folder)
    except media.MediaError as e:
        return f"{m['text']} (not available: {e})"
    line = f"{m['text']} saved to {path}"
    if info["type"] in ("ptt", "audio", "video"):
        line += " (audio and video cannot be played here: tell the user so they can listen on the phone)"
    elif path.suffix in (".xlsx", ".docx"):
        text = office.office_text(path)
        if text:
            txt = path.with_name(path.name + ".txt")
            txt.write_text(text, encoding="utf-8")
            line += f"; its content as text is in {txt}"
    else:
        line += " (open it with Read to see it)"
    return line


def read_thread(workspace_name: str, contact: str, limit: int, attachments: bool = False) -> None:
    roles, people = scope(workspace_name)
    num, person = find_person(people, contact)
    role = roles[person["role"]]
    name = person.get("name", num)
    if not role.get("read", False):
        raise ToolError(f"{name} has the role {person['role']}, which does not allow reading their messages. Tell the user.")
    with session():
        res = js(page(THREAD, number=num, limit=limit, withMedia=attachments), timeout=90)
    if res.get("none"):
        print(f"0 messages: there is no WhatsApp chat with {name} (+{num}).")
        return
    print(f"{len(res['msgs'])} message(s) with {name} (+{num}, role {person['role']}), oldest first; "
          f"{res['unread']} unread (they stay marked unread):")
    if role.get("instructions"):
        print(f"The user's instructions for the role {person['role']}: {role['instructions']}")
    if person.get("note"):
        print(f"The user's note about {name}: {person['note']}")
    fields, _ = person_record(num)
    if fields:
        print("Record: " + "; ".join(f"{k}={v}" for k, v in fields.items()) + f" ({PEOPLE_DIR}/{num}.md)")
    folder = workspace() / MEDIA_DIR / num if attachments else None
    for m in res["msgs"]:
        body = describe_media(m, folder) if m.get("media") else m["text"][:600]
        print(f"{m['when']} | {'you' if m['mine'] else name} | {body}")
    if not attachments and any(re.match(r"\[(voice note|audio|image|video|document)", m["text"]) for m in res["msgs"]):
        print("There are images or files: to look at them, call again with attachments set to true.")


SEND = r"""
  const clean = s => (s || "").replace(/\s+/g, " ").trim();
  let chat = C.Chat.getModelsArray().find(c => pn(c) === number && c.id.server !== "g.us");
  if (!chat) {
    const wid = window.require("WAWebWidFactory").createWid(number + "@c.us");
    const r = await window.require("WAWebFindChatAction").findOrCreateLatestChat(wid);
    chat = r && (r.chat || r);
  }
  if (!chat) return JSON.stringify({error: "no chat"});
  const mine = () => chat.msgs.getModelsArray().filter(m => m.id.fromMe && m.type === "chat");
  // Sending twice puts two copies in front of a real person, so a recent identical message wins.
  const recent = mine().find(m => clean(m.body) === clean(message) && Date.now() / 1000 - m.t < 1800);
  if (recent) return JSON.stringify({already: true, when: fmt(recent.t)});
  await window.require("WAWebSendTextMsgChatAction").sendTextMsgToChat(chat, message);
  for (let i = 0; i < 30; i++) {
    const m = mine().find(m => clean(m.body) === clean(message) && Date.now() / 1000 - m.t < 120);
    if (m && m.ack >= 1) return JSON.stringify({ok: true, when: fmt(m.t)});
    await new Promise(r => setTimeout(r, 500));
  }
  return JSON.stringify({ok: false});
"""


def reply(workspace_name: str, contact: str, message: str) -> None:
    roles, people = scope(workspace_name)
    num, person = find_person(people, contact)
    name = person.get("name", num)
    if not roles[person["role"]].get("reply", False):
        raise ToolError(f"{name} has the role {person['role']}, which does not allow replying from this assistant. "
                        "Tell the user; nothing was sent.")
    if os.environ.get("TANKA_UNATTENDED") and not roles[person["role"]].get("auto_reply", False):
        # Nobody is watching this run: only roles the user opted in may be answered without them.
        raise ToolError(f"The user is not here and the role {person['role']} is not set to auto_reply. Nothing was sent: "
                        "save the reply you propose in .tanka/drafts/ and report it for the user to approve.")
    if not message.strip():
        raise ToolError("The message is empty. Ask the user what to reply.")
    with session():
        res = js(page(SEND, number=num, message=message), timeout=60)
    if res.get("already"):
        print(f"That same message was already sent to {name} ({res['when']}); it was not sent again.")
    elif res.get("ok"):
        print(f"Message sent to {name} (+{num}) at {res['when']} and confirmed by WhatsApp's server. Do not repeat it.")
    elif res.get("error"):
        raise ToolError(f"Could not open a chat with {name} (+{num}). Tell the user; nothing was sent.")
    else:
        raise ToolError(f"The message to {name} was not confirmed within 15 s: it may or may not have gone out. "
                        "Ask the user to check the chat on their phone; do not retry.")


CONTACTS = r"""
  const q = query.toLowerCase(), qd = query.replace(/\D/g, "");
  const out = [];
  for (const ct of C.Contact.getModelsArray()) {
    if (!ct.isAddressBookContact && !ct.name) continue;
    const p = ct.phoneNumber ? (ct.phoneNumber.user || String(ct.phoneNumber).split("@")[0]) : (ct.id.server === "c.us" ? ct.id.user : null);
    if (!p) continue;
    const name = ct.name || ct.pushname || "";
    if ((qd.length >= 4 && p.includes(qd)) || (q && name.toLowerCase().includes(q))) out.push({n: p, name});
    if (out.length >= 40) break;
  }
  return JSON.stringify(out);
"""


def search_contacts(workspace_name: str, query: str) -> None:
    reg = registry()
    roles, _ = scope(workspace_name)
    if len(query.strip()) < 2:
        raise ToolError("Ask for at least two letters of the name or four digits of the number.")
    with session():
        found = js(page(CONTACTS, query=query.strip()), timeout=60)
    seen = {}
    for f in found:
        seen.setdefault(f["n"], f["name"])
    print(f"{len(seen)} address-book contact(s) matching \"{query}\" (number | name on the phone | role):")
    for n, name in list(seen.items())[:20]:
        c = reg["contacts"].get(n)
        if c is None:
            role = "no role"
        elif c.get("role") in roles:
            role = c["role"]
        else:
            role = "role of another assistant"
        print(f"+{n} | {name} | {role}")
    print(f"Only the user assigns roles, in {REGISTRY}.")


def list_people(workspace_name: str, where: str = "", search: str = "") -> None:
    """Every contact of this workspace with the fields of its record, optionally filtered. No browser needed."""
    _, people = scope(workspace_name)
    key, _, want = where.partition("=")
    key, want, q = key.strip().lower(), want.strip().lower(), search.strip().lower()
    if where and not want:
        raise ToolError(f"The filter \"{where}\" must be field=value (e.g. sale_status=quoted).")
    rows = []
    for num, person in sorted(people.items(), key=lambda kv: str(kv[1].get("name", "")).lower()):
        fields, notes = person_record(num)
        if key and want not in fields.get(key, "").lower():
            continue
        if q and q not in (str(person.get("name", "")) + " " + " ".join(fields.values()) + " " + notes).lower():
            continue
        rows.append((num, person, fields))
    what = f" with {key}={want}" if key else ""
    what += f" mentioning \"{search}\"" if q else ""
    print(f"{len(rows)} person(s){what} (+number | name | role | record):")
    for num, person, fields in rows[:40]:
        data = "; ".join(f"{k}={v}" for k, v in fields.items()) or "no record"
        print(f"+{num} | {person.get('name', '?')} | {person['role']} | {data}")
    if len(rows) > 40:
        print(f"(showing 40 of {len(rows)}; narrow the filter)")


# ---------------------------------------------------------------- events (for triggers)

EVENTS = r"""
  // Installed once per page load: WhatsApp adds every arriving message to its Msg collection.
  if (!window.__tankaQueue) {
    window.__tankaQueue = [];
    C.Msg.on("add", m => {
      try {
        if (!m.isNewMsg || m.id.fromMe) return;
        const remote = m.id.remote;
        if (!remote || remote.server === "g.us" || remote.server === "broadcast" || remote.server === "newsletter") return;
        window.__tankaQueue.push({remote: remote._serialized, id: m.id.id, t: m.t});
      } catch (e) {}
    });
    return JSON.stringify({installed: true, events: []});
  }
  const out = [];
  for (const e of window.__tankaQueue.splice(0)) {
    const chat = C.Chat.get(e.remote);
    const n = chat ? pn(chat) : null;
    if (n) out.push({number: n, id: e.id, t: e.t});
  }
  return JSON.stringify({events: out});
"""


def drain_events() -> list[dict]:
    """New incoming messages since the last call, from contacts that have a role. No message text."""
    reg = registry()
    with session():
        res = js(page(EVENTS), timeout=60)
    events = []
    for e in res.get("events", []):
        person = reg["contacts"].get(e["number"])
        role = reg["roles"].get(person.get("role")) if person else None
        if not role:
            continue  # no role: nobody is woken up, and nothing about it leaves this function
        events.append({"source": "whatsapp", "kind": "message", "scope": role.get("workspace"), "role": person["role"],
                       "contact": "+" + e["number"], "name": person.get("name", ""), "id": e["id"], "at": e["t"]})
    return events


def send_alert(number: str, message: str) -> None:
    """Send the user's own alert to the number they configured for it. Used by the automation daemon only."""
    with session():
        res = js(page(SEND, number=digits(number), message=message), timeout=60)
    if not (res.get("ok") or res.get("already")):
        raise ToolError("The alert did not go out.")
