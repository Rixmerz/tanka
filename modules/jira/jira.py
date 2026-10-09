"""Jira Cloud for Tanka: search, read, create, transition, comment on and link issues, per workspace.

What each assistant may touch is decided here, never by the model:

- `scopes.json` (in TANKA_JIRA_HOME) maps a workspace to the Jira projects it may
  use and whether it may write. The user edits it; the assistants cannot, because
  they only write inside their own workspace.
- Every key, project and JQL query is checked against that file before any
  network call. JQL is always wrapped as `project in (...) AND (<query>)`.

The API token is read at call time from the environment variable named by
TANKA_JIRA_TOKEN_VAR (default JIRA_API_TOKEN). It never appears in output,
errors or files. The other settings (site, email, the variable's name, a CA
bundle) can live in `config.json` beside `scopes.json`; an environment variable
always wins over the file. When the token is not in the environment, a
`token_wrapper` in that file (a command that injects it, such as a secrets
manager's "run with these secrets") launches this same script once.
"""
import base64
import contextlib
import fcntl
import json
import os
import re
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Every setting has a default and an environment variable; see README.md.
HOME = Path(os.environ.get("TANKA_JIRA_HOME", Path.home() / ".tanka" / "shared" / "jira"))
SCOPES = HOME / "scopes.json"
CONFIG = HOME / "config.json"
WRAPPED = "TANKA_JIRA_WRAPPED"  # set on the relaunch, so a wrapper that fails cannot loop
MAX_OUTPUT = 5800  # under tanka_tools.MAX_OUTPUT_CHARS (6000), leaving room for the marker
RETRY_STATUSES = (429, 502, 503)
MAX_TRIES = 4
KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]+)-[1-9][0-9]*$")
PROJECT_RE = re.compile(r"^[A-Z][A-Z0-9_]+$")
TRUNCATED = "\n[... output truncated: narrow the request (a smaller limit or a more specific query).]"


class ToolError(Exception):
    """A failure the model should relay: the message says what to do next."""


def run(main) -> None:
    relaunch_with_token()
    try:
        out = main(json.load(sys.stdin))
        if out:
            print(cap(out))
    except ToolError as e:
        sys.exit(redact(str(e)))


def cap(text: str, limit: int = MAX_OUTPUT) -> str:
    return text if len(text) <= limit else text[:limit] + TRUNCATED


# ---------------------------------------------------------------- configuration

def file_config() -> dict:
    """The non-secret settings the user keeps in config.json (nothing here is a token)."""
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def setting(env_name: str, key: str, default: str = "") -> str:
    """An environment variable, else the file's key, else the default."""
    return os.environ.get(env_name, "").strip() or str(file_config().get(key) or "").strip() or default


def token_var() -> str:
    return setting("TANKA_JIRA_TOKEN_VAR", "token_var", "JIRA_API_TOKEN")


def relaunch_with_token() -> None:
    """Run this same script under the user's wrapper when the token is not in the environment.

    The wrapper (an argv prefix in config.json, for example a secrets manager's exec command) injects the token
    variable; nothing is read or printed here, and stdin is untouched so the tool's arguments still arrive. It
    happens once: a wrapper that is missing or fails ends in the normal "token is not set" error.
    """
    if os.environ.get(token_var()) or os.environ.get(WRAPPED):
        return
    wrapper = file_config().get("token_wrapper")
    if not (isinstance(wrapper, list) and wrapper and all(isinstance(a, str) and a for a in wrapper)):
        return
    try:
        os.execvpe(wrapper[0], [*wrapper, sys.executable, *sys.argv], {**os.environ, WRAPPED: "1"})
    except OSError:
        return


def token() -> str:
    t = os.environ.get(token_var(), "")
    if not t:
        raise ToolError(f"The Jira API token is not set (environment variable {token_var()}). Tell the user; do not retry.")
    return t


def redact(text: str) -> str:
    t = os.environ.get(token_var(), "")
    return text.replace(t, "***") if t else text


def config() -> dict:
    site = setting("TANKA_JIRA_SITE", "site").rstrip("/")
    email = setting("TANKA_JIRA_EMAIL", "email")
    missing = [n for n, v in (("site", site), ("email", email)) if not v]
    if missing:
        raise ToolError(f"Jira is not configured ({', '.join(missing)} not set in config.json or the environment). Tell the user; do not retry.")
    if not site.startswith("https://"):
        raise ToolError("The Jira site must start with https:// (e.g. https://your-company.atlassian.net). Tell the user.")
    return {"site": site, "email": email}


# ---------------------------------------------------------------- scope

def scopes() -> dict:
    if not SCOPES.is_file():
        raise ToolError(f"{SCOPES} does not exist: the user has not given any workspace a Jira project yet. Tell the user.")
    try:
        data = json.loads(SCOPES.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ToolError(f"{SCOPES} is not valid JSON (line {e.lineno}). Tell the user; Jira cannot be used until they fix it.")
    s = data.get("scopes") if isinstance(data, dict) else None
    return s if isinstance(s, dict) else {}


def scope_of(scope: str) -> dict:
    entry = scopes().get(scope)
    projects = []
    if isinstance(entry, dict) and isinstance(entry.get("projects"), list):
        projects = sorted({str(p).strip().upper() for p in entry["projects"] if PROJECT_RE.match(str(p).strip().upper())})
    if not projects:
        raise ToolError("No Jira project is assigned to this assistant. Tell the user: only they can assign one, in their scopes file.")
    return {"projects": projects, "write": entry.get("write") is True, "unattended_write": entry.get("unattended_write") is True}


def check_project(scope: str, project: str, write: bool = False) -> str:
    s = scope_of(scope)
    p = (project or "").strip().upper()
    if p not in s["projects"]:
        raise ToolError(f"\"{project}\" is not a Jira project this assistant may use. It may use: {', '.join(s['projects'])}.")
    if write and not s["write"]:
        raise ToolError("This assistant may only read Jira, not change it. Tell the user; only they can allow writing.")
    # A message typed in the page's chat runs unattended by the launcher's definition, but the user is there
    # reading the answer: the page marks it with TANKA_CHAT. Routines and triggers never carry that mark.
    if write and os.environ.get("TANKA_UNATTENDED") and not os.environ.get("TANKA_CHAT") and not s["unattended_write"]:
        # Tanka's own tools are pre-approved, so nothing else stops a routine or a delegated run from writing.
        raise ToolError("Nobody is watching this run, so it may not change Jira. Tell the user what you would have done; "
                        "only they can allow unattended writes (unattended_write in their scopes file).")
    return p


def check_key(scope: str, key: str, write: bool = False) -> str:
    k = (key or "").strip().upper()
    m = KEY_RE.match(k)
    if not m:
        raise ToolError(f"\"{key}\" is not an issue key. Use the form PROJ-123, as jira_search shows it.")
    check_project(scope, m.group(1), write)
    return k


def _strip_strings(jql: str) -> str:
    """The JQL with the contents of quoted strings removed, so parentheses inside them do not count."""
    out, quote, i = [], None, 0
    while i < len(jql):
        ch = jql[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
                out.append(ch)
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        else:
            out.append(ch)
        i += 1
    if quote:
        raise ToolError("The JQL has an unclosed quote. Fix the query and call again.")
    return "".join(out)


def wrap_jql(scope: str, jql: str) -> str:
    """`project in (A,B) AND (<jql>) [ORDER BY ...]`, or a ToolError if the query could escape the wrap."""
    projects = scope_of(scope)["projects"]
    jql = (jql or "").strip()
    bare = _strip_strings(jql)
    order = ""
    m = re.search(r"\border\s+by\b", bare, re.IGNORECASE)
    if m:
        if len(re.findall(r"\border\s+by\b", bare, re.IGNORECASE)) > 1:
            raise ToolError("The JQL has ORDER BY more than once. Use it once, at the end.")
        order_bare = bare[m.end():]
        if not re.fullmatch(r"[\w\s,.\"'-]*", order_bare) or re.search(r"\b(and|or|not)\b", order_bare, re.IGNORECASE):
            raise ToolError("ORDER BY must come last and list only fields (e.g. ORDER BY updated DESC).")
        # `bare` has quoted text removed, so map the cut back to the original string.
        cut = _original_index(jql, m.start())
        filt, order = jql[:cut].strip(), jql[cut:].strip()
        bare = bare[:m.start()]
    else:
        filt = jql
    depth = 0
    for ch in bare:
        depth += ch == "("
        depth -= ch == ")"
        if depth < 0:
            break
    if depth != 0:
        raise ToolError("The JQL has unbalanced parentheses. Fix the query and call again.")
    if ";" in bare:
        raise ToolError("The JQL may not contain ';'. Fix the query and call again.")
    base = f"project in ({', '.join(projects)})"
    wrapped = f"{base} AND ({filt})" if filt else base
    return f"{wrapped} {order}" if order else wrapped


def _original_index(jql: str, bare_index: int) -> int:
    """Index in `jql` that corresponds to `bare_index` in `_strip_strings(jql)`."""
    quote, i, b = None, 0, 0
    while i < len(jql) and b < bare_index:
        ch = jql[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
                b += 1
        elif ch in "\"'":
            quote = ch
            b += 1
        else:
            b += 1
        i += 1
    return i


# ---------------------------------------------------------------- ADF

def to_adf(text: str) -> dict:
    """Plain text to an Atlassian Document: paragraphs on blank lines, hard breaks on single newlines."""
    paragraphs = [p for p in re.split(r"\n\s*\n", (text or "").replace("\r\n", "\n")) if p.strip()]
    content = []
    for p in paragraphs:
        nodes = []
        for i, line in enumerate(p.strip("\n").split("\n")):
            if i:
                nodes.append({"type": "hardBreak"})
            if line:
                nodes.append({"type": "text", "text": line})
        content.append({"type": "paragraph", "content": nodes})
    return {"type": "doc", "version": 1, "content": content}


BLOCKS = {"paragraph", "heading", "blockquote", "codeBlock", "panel", "rule", "table", "tableRow", "mediaSingle"}


def from_adf(node) -> str:
    """An Atlassian Document (or any node of one) to plain text."""
    if not node:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "".join(from_adf(n) for n in node)
    t = node.get("type")
    if t == "text":
        return node.get("text", "")
    if t == "hardBreak":
        return "\n"
    if t == "mention":
        return node.get("attrs", {}).get("text", "@someone")
    if t in ("emoji", "status"):
        return node.get("attrs", {}).get("text", "")
    if t in ("inlineCard", "blockCard"):
        return node.get("attrs", {}).get("url", "")
    if t == "rule":
        return "---"
    kids = node.get("content", [])
    if t == "doc":
        return "\n\n".join(s for s in (from_adf(k).strip("\n") for k in kids) if s)
    if t in ("bulletList", "orderedList"):
        lines = []
        for i, item in enumerate(kids, 1):
            mark = f"{i}." if t == "orderedList" else "-"
            lines.append(f"{mark} " + "\n".join(from_adf(c) for c in item.get("content", [])).strip())
        return "\n".join(lines)
    if t == "tableRow":
        return " | ".join(from_adf(c).strip() for c in kids)
    if t == "table":
        return "\n".join(from_adf(c) for c in kids)
    if t in BLOCKS or t in ("listItem", "tableCell", "tableHeader", "expand"):
        return "\n".join(filter(None, (from_adf(c) for c in kids))) if t not in ("paragraph", "heading") else from_adf(kids)
    return from_adf(kids)


# ---------------------------------------------------------------- HTTP

def ssl_context():
    ca = setting("TANKA_JIRA_CA_FILE", "ca_file")
    return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()


def _default_opener(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


OPENER = _default_opener
SLEEP = time.sleep


def request(method: str, path: str, body=None, query: dict | None = None):
    """One call to the Jira REST API, retried on 429/502/503; the parsed JSON (or None)."""
    cfg = config()
    auth = base64.b64encode(f"{cfg['email']}:{token()}".encode()).decode()
    url = cfg["site"] + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    for attempt in range(1, MAX_TRIES + 1):
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Basic {auth}", "Accept": "application/json", "Content-Type": "application/json"})
        try:
            with OPENER(req, 60) as resp:
                raw = resp.read()
            return json.loads(raw) if raw and raw.strip() else None
        except urllib.error.HTTPError as e:
            if e.code in RETRY_STATUSES and attempt < MAX_TRIES:
                e.close()
                SLEEP(_retry_after(e.headers.get("Retry-After") if e.headers else None, attempt))
                continue
            try:
                text = e.read().decode("utf-8", "replace")
            except OSError:
                text = ""
            finally:
                e.close()
            raise ToolError(redact(f"Jira answered HTTP {e.code}: {_short(text)}. Tell the user; do not retry."))
        except urllib.error.URLError as e:
            raise ToolError(redact(f"Could not reach Jira ({e.reason}). Tell the user; do not retry."))
        except (TimeoutError, OSError) as e:
            raise ToolError(redact(f"Could not reach Jira ({type(e).__name__}). Tell the user; do not retry."))
    raise ToolError("Jira is busy. Tell the user to try again in a minute.")


def _retry_after(value, attempt: int) -> float:
    try:
        return min(max(float(value), 0.0), 10.0)
    except (TypeError, ValueError):
        return float(2 ** attempt)


def _short(text: str) -> str:
    try:
        d = json.loads(text)
        msgs = list(d.get("errorMessages") or []) + [f"{k}: {v}" for k, v in (d.get("errors") or {}).items()]
        if msgs:
            text = "; ".join(map(str, msgs))
    except (ValueError, AttributeError):
        pass
    return " ".join(text.split())[:300] or "no details"


# ---------------------------------------------------------------- tools

def _name(user) -> str:
    return (user or {}).get("displayName") or "unassigned"


def search(scope: str, jql: str | None = None, limit: int = 20, key: str | None = None) -> str:
    if key:
        return issue(scope, key)
    if not (jql or "").strip():
        raise ToolError("Give a JQL query to search, or the key of one issue to read it in full.")
    limit = max(1, min(int(limit or 20), 50))
    q = wrap_jql(scope, jql)
    issues, page = [], None
    while len(issues) < limit:
        body = {"jql": q, "maxResults": limit - len(issues), "fields": ["summary", "status", "issuetype", "assignee"]}
        if page:
            body["nextPageToken"] = page
        res = request("POST", "/rest/api/3/search/jql", body) or {}
        issues += res.get("issues") or []
        page = res.get("nextPageToken")
        if not page or not res.get("issues"):
            break
    issues = issues[:limit]
    if not issues:
        return "0 issue(s) match. Try a broader query."
    lines = [f"{len(issues)} issue(s){' (more exist; narrow the query)' if page else ''}: KEY | status | type | assignee | summary"]
    for i in issues:
        f = i.get("fields") or {}
        lines.append(" | ".join([i.get("key", "?"), (f.get("status") or {}).get("name", "?"),
                                 (f.get("issuetype") or {}).get("name", "?"), _name(f.get("assignee")),
                                 " ".join((f.get("summary") or "").split())]))
    return "\n".join(lines)


def issue(scope: str, key: str) -> str:
    key = check_key(scope, key)
    fields = "summary,status,issuetype,assignee,labels,parent,description,comment,issuelinks"
    d = request("GET", f"/rest/api/3/issue/{key}", query={"fields": fields}) or {}
    f = d.get("fields") or {}
    parent = f.get("parent") or {}
    out = [f"{d.get('key', key)}: {f.get('summary', '')}",
           f"Status: {(f.get('status') or {}).get('name', '?')} | Type: {(f.get('issuetype') or {}).get('name', '?')}"
           f" | Assignee: {_name(f.get('assignee'))}",
           f"Labels: {', '.join(f.get('labels') or []) or 'none'}",
           f"Parent: {parent.get('key', 'none')}{' - ' + parent['fields']['summary'] if parent.get('fields', {}).get('summary') else ''}"]
    links = []
    for link in f.get("issuelinks") or []:
        t = link.get("type") or {}
        if link.get("outwardIssue"):
            links.append(f"{t.get('outward', 'relates to')} {link['outwardIssue'].get('key')}")
        elif link.get("inwardIssue"):
            links.append(f"{t.get('inward', 'relates to')} {link['inwardIssue'].get('key')}")
    out.append(f"Links: {'; '.join(links) or 'none'}")
    comments = ((f.get("comment") or {}).get("comments") or [])[-10:]
    # Comments go last so that truncation, if any, cuts the oldest detail rather than the header.
    desc = from_adf(f.get("description")).strip() or "(no description)"
    out.append("Description:\n" + cap(desc, 2500))
    out.append(f"Last {len(comments)} comment(s):" if comments else "No comments.")
    for c in comments:
        out.append(f"- {_name(c.get('author'))}, {(c.get('created') or '')[:10]}: {cap(from_adf(c.get('body')).strip(), 600)}")
    return "\n".join(out)


def create(scope: str, project: str, type_: str, summary: str, description: str | None = None,
           labels: str | None = None, parent: str | None = None) -> str:
    project = check_project(scope, project, write=True)
    if parent:
        parent = check_key(scope, parent, write=True)
    fields = {"project": {"key": project}, "issuetype": {"name": type_}, "summary": summary.strip()}
    if description:
        fields["description"] = to_adf(description)
    labs = _labels(labels)
    if labs:
        fields["labels"] = labs
    if parent:
        fields["parent"] = {"key": parent}
    res = request("POST", "/rest/api/3/issue", {"fields": fields}) or {}
    return f"Created {res.get('key', '?')} in {project}: {summary.strip()}. Do not call jira_create again for it."


def _labels(text: str | None) -> list[str]:
    return [x.strip().replace(" ", "-") for x in (text or "").split(",") if x.strip()]


def _pick_transition(key: str, status: str) -> dict:
    res = request("GET", f"/rest/api/3/issue/{key}/transitions") or {}
    options = res.get("transitions") or []
    want = (status or "").strip().lower()
    match = [t for t in options if ((t.get("to") or {}).get("name") or "").lower() == want] \
        or [t for t in options if (t.get("name") or "").lower() == want]
    if not match:
        avail = sorted({(t.get("to") or {}).get("name") or t.get("name", "?") for t in options})
        raise ToolError(f"{key} cannot move to \"{status}\" from where it is. Available: {', '.join(avail) or 'none'}. "
                        "Ask the user which one.")
    return match[0]


def _moved(t: dict) -> str:
    return (t.get("to") or {}).get("name") or t.get("name")


def update(scope: str, key: str, status: str | None = None, summary: str | None = None,
           description: str | None = None, add_labels: str | None = None, remove_labels: str | None = None) -> str:
    """Change an existing issue: its status, summary, description and labels (added or removed, never replaced)."""
    key = check_key(scope, key, write=True)
    fields, done = {}, []
    if (summary or "").strip():
        fields["summary"] = summary.strip()
        done.append("summary")
    if (description or "").strip():
        fields["description"] = to_adf(description)
        done.append("description")
    ops = [{"add": x} for x in _labels(add_labels)] + [{"remove": x} for x in _labels(remove_labels)]
    if ops:
        done.append("labels")
    move = _pick_transition(key, status) if (status or "").strip() else None  # checked before anything is changed
    if not done and not move:
        raise ToolError("Nothing to change. Ask the user what to change: status, summary, description or labels.")
    if fields or ops:
        body = {}
        if fields:
            body["fields"] = fields
        if ops:
            body["update"] = {"labels": ops}
        request("PUT", f"/rest/api/3/issue/{key}", body)
    if move:
        request("POST", f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": move["id"]}})
        done.append(f"status -> {_moved(move)}")
    return f"{key} updated ({', '.join(done)}). Do not call jira_update again for the same change."


def transition(scope: str, key: str, status: str) -> str:
    key = check_key(scope, key, write=True)
    t = _pick_transition(key, status)
    request("POST", f"/rest/api/3/issue/{key}/transitions", {"transition": {"id": t["id"]}})
    return f"{key} moved to {_moved(t)}."


# ---------------------------------------------------------------- deleting: the assistant asks, the user clicks

DELETIONS = HOME / "deletions"
MAX_DELETIONS_KEPT = 30
SCOPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")


def deletions_file(scope: str) -> Path:
    if not SCOPE_RE.fullmatch(scope or ""):
        raise ToolError(f"'{scope}' is not a workspace scope. Tell the user.")
    return DELETIONS / f"{scope}.json"


@contextlib.contextmanager
def deletions_locked(scope: str):
    f = deletions_file(scope)
    f.parent.mkdir(parents=True, exist_ok=True)
    with open(f.with_suffix(".lock"), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def read_deletions(scope: str) -> list[dict]:
    try:
        data = json.loads(deletions_file(scope).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items = data.get("deletions") if isinstance(data, dict) else None
    return [d for d in items if isinstance(d, dict) and d.get("id")] if isinstance(items, list) else []


def write_deletions(scope: str, items: list[dict]) -> None:
    f = deletions_file(scope)
    f.parent.mkdir(parents=True, exist_ok=True)
    pending = [d for d in items if d.get("status") == "pending"]
    rest = [d for d in items if d.get("status") != "pending"][:MAX_DELETIONS_KEPT]
    tmp = f.with_name(f".{f.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"deletions": pending + rest}, indent=2) + "\n", encoding="utf-8")
    tmp.replace(f)


def request_delete(scope: str, key: str) -> dict:
    """Leave a request for the user to confirm on the page. Nothing is deleted here, ever."""
    key = check_key(scope, key, write=True)
    f = (request("GET", f"/rest/api/3/issue/{key}", query={"fields": "summary"}) or {}).get("fields") or {}
    with deletions_locked(scope):
        items = read_deletions(scope)
        if any(d["key"] == key and d.get("status") == "pending" for d in items):
            raise ToolError(f"A request to delete {key} is already waiting for the user. Tell them to confirm it on the page.")
        d = {"id": "d-" + secrets.token_hex(3), "key": key, "summary": " ".join((f.get("summary") or "").split())[:200],
             "t": round(time.time(), 3), "status": "pending"}
        write_deletions(scope, [d] + items)
    return d


def _find(items: list[dict], did: str) -> dict:
    d = next((x for x in items if x["id"] == did), None)
    if d is None or d.get("status") != "pending":
        raise ToolError(f"No pending request {did} in this workspace.")
    return d


def confirm_delete(scope: str, did: str) -> str:
    """The user's click: delete the issue for good (Jira refuses when it has subtasks)."""
    with deletions_locked(scope):
        items = read_deletions(scope)
        d = _find(items, did)
        check_key(scope, d["key"], write=True)
        request("DELETE", f"/rest/api/3/issue/{d['key']}")
        d["status"], d["closed"] = "deleted", round(time.time(), 3)
        write_deletions(scope, items)
    return f"Deleted {d['key']}. It cannot be undone."


def dismiss_delete(scope: str, did: str) -> str:
    with deletions_locked(scope):
        items = read_deletions(scope)
        d = _find(items, did)
        d["status"], d["closed"] = "dismissed", round(time.time(), 3)
        write_deletions(scope, items)
    return f"Kept {d['key']}; nothing was deleted."


def delete_tool(scope: str, key: str) -> str:
    d = request_delete(scope, key)
    return (f"Request {d['id']} saved to delete {d['key']} ({d['summary']}). NOTHING was deleted: the user must press "
            "Delete in the chat. Tell them, and do not say it is deleted until they confirm.")


def comment(scope: str, key: str, body: str) -> str:
    key = check_key(scope, key, write=True)
    if not (body or "").strip():
        raise ToolError("The comment is empty. Ask the user what to write.")
    request("POST", f"/rest/api/3/issue/{key}/comment", {"body": to_adf(body)})
    return f"Comment added to {key}. It is visible to everyone on the issue; do not call jira_comment again for it."


def link(scope: str, from_key: str, to_key: str, link_type: str = "Blocks") -> str:
    a = check_key(scope, from_key, write=True)
    b = check_key(scope, to_key, write=True)
    if a == b:
        raise ToolError("An issue cannot be linked to itself. Ask the user for the other key.")
    # Jira reads the link as '<inwardIssue> <outward verb> <outwardIssue>' (e.g. PROJ-1 blocks PROJ-2).
    request("POST", "/rest/api/3/issueLink", {"type": {"name": link_type or "Blocks"},
                                             "inwardIssue": {"key": a}, "outwardIssue": {"key": b}})
    return f"Linked: {a} {(link_type or 'Blocks').lower()} {b}."
