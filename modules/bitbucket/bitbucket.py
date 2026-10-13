"""Bitbucket Cloud for Tanka: repositories, commits and pipelines, per workspace; running or stopping a pipeline
waits for the user's Approve click unless they listed that repository (and pipeline) as bypass.

What each assistant may touch is decided here, never by the model:

- `scopes.json` (in TANKA_BITBUCKET_HOME) maps a Tanka workspace to the repositories it may read (`repos`: a list
  of slugs, or "*" for every repository the token sees) and to those where it may act directly (`bypass`: a
  repository slug mapped to "*", any pipeline, or to a list of custom pipeline names). Everything else that acts
  (run or stop a pipeline) only leaves a request with the approvals module, and the user's Approve click runs it.
  The user edits the file; the assistants cannot, because they only write inside their own workspace.
- Every repository, ref and pipeline is checked against that file before any network call.

The token is read at call time from the environment variable named by TANKA_BITBUCKET_TOKEN_VAR (default
BITBUCKET_API_TOKEN). It never appears in output, errors or files. With an `email` configured it is sent as Basic
email:token (an Atlassian API token); without one, as a Bearer token (a workspace, project or repository access
token). The other settings (workspace, email, the variable's name, a CA bundle) can live in `config.json` beside
`scopes.json`; an environment variable always wins over the file. When the token is not in the environment, a
`token_wrapper` in that file (a command that injects it, such as a secrets manager's "run with these secrets")
launches this same script once.
"""
import base64
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Every setting has a default and an environment variable; see README.md.
HOME = Path(os.environ.get("TANKA_BITBUCKET_HOME", Path.home() / ".tanka" / "shared" / "bitbucket"))
SCOPES = HOME / "scopes.json"
CONFIG = HOME / "config.json"
API = "https://api.bitbucket.org/2.0"
WEB = "https://bitbucket.org"
WRAPPED = "TANKA_BITBUCKET_WRAPPED"  # set on the relaunch, so a wrapper that fails cannot loop
MAX_OUTPUT = 5800  # under tanka_tools.MAX_OUTPUT_CHARS (6000), leaving room for the marker
RETRY_STATUSES = (429, 502, 503)
MAX_TRIES = 4
LOG_LINES = 150
LOG_BYTES = 400_000  # only the end of a step's log is fetched
MAX_PAGES = 10
ALL = "*"
WORKSPACE_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,61}$")
REPO_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,99}$")
REF_RE = re.compile(r"^(?!.*\.\.)(?!/)[A-Za-z0-9._/+-]{1,200}$")
PIPELINE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ /*+-]{0,99}$")
HASH_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
NAME_FILTER_RE = re.compile(r"^[A-Za-z0-9 ._/-]{1,60}$")
TRUNCATED = "\n[... output truncated: narrow the request (a smaller limit, a ref, or a grep filter).]"
NO_RETRY = "Tell the user; do not retry."


class ToolError(Exception):
    """A failure the model should relay: the message says what to do next."""


class NeedsApproval(ToolError):
    """An action the assistant may take only after the user's Approve click."""

    def __init__(self, scope: str, repo: str, what: str):
        super().__init__(f"{what} in {repo} needs the user's approval.")
        self.scope, self.repo = scope, repo


def run(main) -> None:
    relaunch_with_token()
    args = json.load(sys.stdin)
    try:
        out = main(args)
        if out:
            print(cap(out))
    except NeedsApproval as e:
        try:
            print(ask_approval(e.scope, e.repo, os.environ.get("TANKA_TOOL", ""), args))
        except ToolError as e2:
            sys.exit(redact(str(e2)))
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
    return setting("TANKA_BITBUCKET_TOKEN_VAR", "token_var", "BITBUCKET_API_TOKEN")


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
        raise ToolError(f"The Bitbucket token is not set (environment variable {token_var()}). {NO_RETRY}")
    return t


SECRET_PATTERNS = [
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 ***"),
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1***@"),
    (re.compile(r"(?i)\b([A-Z0-9_]*(?:password|passwd|secret|token|api[_-]?key|private[_-]?key|credential)[A-Z0-9_]*)"
                r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"), r"\1\2***"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "***"),
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|xox[abpr]-[A-Za-z0-9-]{10,}|ATATT[A-Za-z0-9_=-]{20,})"), "***"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{5,}"), "***"),
]


def redact(text: str) -> str:
    t = os.environ.get(token_var(), "")
    return text.replace(t, "***") if t else text


def redact_log(text: str) -> str:
    """A log line with the token and anything that looks like a credential replaced by ***."""
    text = redact(text)
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def config() -> dict:
    ws = setting("TANKA_BITBUCKET_WORKSPACE", "workspace").lower()
    if not ws:
        raise ToolError(f"Bitbucket is not configured (workspace not set in config.json or the environment). {NO_RETRY}")
    if not WORKSPACE_RE.match(ws):
        raise ToolError(f"The Bitbucket workspace \"{ws}\" is not a workspace id (e.g. acme). Tell the user.")
    return {"workspace": ws, "email": setting("TANKA_BITBUCKET_EMAIL", "email")}


# ---------------------------------------------------------------- scope

def scopes() -> dict:
    if not SCOPES.is_file():
        raise ToolError(f"{SCOPES} does not exist: the user has not given any workspace a Bitbucket repository yet. "
                        "Tell the user.")
    try:
        data = json.loads(SCOPES.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ToolError(f"{SCOPES} is not valid JSON (line {e.lineno}). Tell the user; Bitbucket cannot be used until "
                        "they fix it.")
    s = data.get("scopes") if isinstance(data, dict) else None
    return s if isinstance(s, dict) else {}


def _repos(value) -> set | str:
    if value == ALL or value == [ALL]:
        return ALL
    if not isinstance(value, list):
        return set()
    return {str(r).strip().lower() for r in value if REPO_RE.match(str(r).strip().lower())}


def _bypass(value, readable) -> dict:
    """{repo: ALL or set of pipeline names}, only for repositories the scope may read."""
    out = {}
    if not isinstance(value, dict):
        return out
    for repo, pipes in value.items():
        r = str(repo).strip().lower()
        if not REPO_RE.match(r) or not (readable == ALL or r in readable):
            continue
        if pipes == ALL or pipes == [ALL]:
            out[r] = ALL
        elif isinstance(pipes, list):
            names = {str(p).strip() for p in pipes if str(p).strip()}
            if names:
                out[r] = names
    return out


def scope_of(scope: str) -> dict:
    entry = scopes().get(scope)
    entry = entry if isinstance(entry, dict) else {}
    read = _repos(entry.get("repos"))
    if not read:
        raise ToolError("No Bitbucket repository is assigned to this assistant. Tell the user: only they can assign "
                        "one, in their scopes file.")
    return {"repos": ALL if read == ALL else sorted(read), "bypass": _bypass(entry.get("bypass"), read),
            "unattended_write": entry.get("unattended_write") is True}


def describe(repos) -> str:
    return "every repository" if repos == ALL else (", ".join(sorted(repos)) or "none")


def check_repo(scope: str, repo: str) -> str:
    """The repository slug, or a ToolError when this scope may not read it."""
    s = scope_of(scope)
    r = (repo or "").strip().lower()
    if not REPO_RE.match(r) or not (s["repos"] == ALL or r in s["repos"]):
        raise ToolError(f"\"{repo}\" is not a Bitbucket repository this assistant may use. It may use: "
                        f"{describe(s['repos'])}. Use bitbucket_repos to see them.")
    return r


def check_ref(ref: str) -> str:
    r = (ref or "").strip()
    if not REF_RE.match(r) or r.endswith("/") or r.endswith(".lock"):
        raise ToolError(f"\"{ref}\" is not a branch name (e.g. main or release/1.2). Ask the user which branch.")
    return r


def check_pipeline(name: str) -> str:
    p = (name or "").strip()
    if not PIPELINE_RE.match(p):
        raise ToolError(f"\"{name}\" is not a custom pipeline name. Use bitbucket_pipelines with options=true to see "
                        "the ones that can be run.")
    return p


def bypassed(s: dict, repo: str, pipeline: str | None) -> bool:
    """Whether the user lets this scope act on that repository (and pipeline) without asking."""
    allowed = s["bypass"].get(repo)
    if allowed is None:
        return False
    return allowed == ALL or (pipeline is not None and pipeline in allowed)


APPROVED = False  # set only by replay(), which the user's Approve click runs through approve.py


def check_action(scope: str, repo: str, pipeline: str | None, what: str) -> str:
    """The repository slug when this action may run now; NeedsApproval when it must wait for the user's click."""
    s = scope_of(scope)
    repo = check_repo(scope, repo)
    if APPROVED:
        return repo
    if not bypassed(s, repo, pipeline):
        raise NeedsApproval(scope, repo, what)
    # A message typed in the page's chat runs unattended by the launcher's definition, but the user is there
    # reading the answer: the page marks it with TANKA_CHAT. Routines and triggers never carry that mark, so there
    # even a bypass only leaves a request, unless the user opted in for this workspace.
    if os.environ.get("TANKA_UNATTENDED") and not os.environ.get("TANKA_CHAT") and not s["unattended_write"]:
        raise NeedsApproval(scope, repo, what)
    return repo


# ---------------------------------------------------------------- HTTP

def ssl_context():
    ca = setting("TANKA_BITBUCKET_CA_FILE", "ca_file")
    return ssl.create_default_context(cafile=ca) if ca else ssl.create_default_context()


def _default_opener(req, timeout):
    return urllib.request.urlopen(req, timeout=timeout, context=ssl_context())


OPENER = _default_opener
SLEEP = time.sleep


def auth_header() -> str:
    email = config()["email"]
    if email:
        return "Basic " + base64.b64encode(f"{email}:{token()}".encode()).decode()
    return f"Bearer {token()}"


def _quote(part: str) -> str:
    return urllib.parse.quote(part, safe="")


def repo_path(repo: str, rest: str = "") -> str:
    return f"/repositories/{_quote(config()['workspace'])}/{_quote(repo)}{rest}"


def request(method: str, path: str, body=None, query: dict | None = None, raw: bool = False,
            headers: dict | None = None):
    """One call to the Bitbucket API, retried on 429/502/503: parsed JSON (or None), or the text when raw.

    `path` is under the API base, or a full `next` link the API returned (which must stay under the base)."""
    # A `next` link is followed only when it is under the API; anything else is taken as a path below it.
    url = path if path.startswith(API + "/") else API + path
    if query:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    data = json.dumps(body).encode() if body is not None else None
    hdrs = {"Authorization": auth_header(), "Accept": "text/plain" if raw else "application/json",
            "Content-Type": "application/json", **(headers or {})}
    for attempt in range(1, MAX_TRIES + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with OPENER(req, 60) as resp:
                content = resp.read()
            if raw:
                return content.decode("utf-8", "replace")
            return json.loads(content) if content and content.strip() else None
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
            raise ToolError(redact(f"Bitbucket answered HTTP {e.code}: {_short(text)}. {_hint(e.code)}"))
        except urllib.error.URLError as e:
            raise ToolError(redact(f"Could not reach Bitbucket ({e.reason}). {NO_RETRY}"))
        except (TimeoutError, OSError) as e:
            raise ToolError(redact(f"Could not reach Bitbucket ({type(e).__name__}). {NO_RETRY}"))
    raise ToolError("Bitbucket is busy. Tell the user to try again in a minute.")


def _hint(code: int) -> str:
    if code == 404:
        return "Check the name with bitbucket_repos or bitbucket_pipelines; do not guess another."
    if code in (401, 403):
        return "The token lacks access or a scope; tell the user, do not retry."
    return NO_RETRY


def _retry_after(value, attempt: int) -> float:
    try:
        return min(max(float(value), 0.0), 10.0)
    except (TypeError, ValueError):
        return float(2 ** attempt)


def _short(text: str) -> str:
    try:
        d = json.loads(text)
        err = d.get("error") or {}
        msg = " ".join(str(x) for x in (err.get("message"), err.get("detail")) if x)
        if msg:
            text = msg
    except (ValueError, AttributeError):
        pass
    return " ".join(text.split())[:300] or "no details"


def paged(path: str, query: dict, limit: int, keep=lambda v: True) -> tuple[list, bool]:
    """Up to `limit` values that `keep` accepts, following `next` links; and whether more exist."""
    found, url, q = [], path, dict(query)
    for _ in range(MAX_PAGES):
        res = request("GET", url, query=q) or {}
        found += [v for v in res.get("values") or [] if keep(v)]
        url, q = res.get("next"), None
        if len(found) > limit or not url:
            break
    return found[:limit], bool(url) or len(found) > limit


# ---------------------------------------------------------------- formatting

def _one_line(text, n: int = 120) -> str:
    return " ".join(str(text or "").split())[:n]


def _who(obj) -> str:
    obj = obj or {}
    return obj.get("display_name") or obj.get("nickname") or "?"


def _author(commit: dict) -> str:
    a = commit.get("author") or {}
    if a.get("user"):
        return _who(a["user"])
    return re.sub(r"\s*<[^>]*>", "", a.get("raw") or "").strip() or "?"


def _duration(seconds) -> str:
    try:
        s = int(seconds)
    except (TypeError, ValueError):
        return "-"
    return f"{s // 60}m{s % 60:02d}s" if s >= 60 else f"{s}s"


def _state(obj: dict) -> str:
    st = obj.get("state") or {}
    name = st.get("name") or "?"
    detail = (st.get("result") or st.get("stage") or {}).get("name")
    return f"{name}/{detail}" if detail else name


def _selector(target: dict) -> str:
    sel = (target or {}).get("selector") or {}
    t = sel.get("type") or "?"
    return f"{t}:{sel['pattern']}" if sel.get("pattern") else t


def _custom_name(target: dict) -> str | None:
    sel = (target or {}).get("selector") or {}
    return sel.get("pattern") if sel.get("type") == "custom" else None


def access_text(s: dict, repo: str) -> str:
    allowed = s["bypass"].get(repo)
    if allowed is None:
        return "actions after Approve"
    if allowed == ALL:
        return "bypass: any pipeline"
    return f"bypass: {', '.join(sorted(allowed))}; others after Approve"


# ---------------------------------------------------------------- read tools

def repos(scope: str, name: str | None = None, limit: int | None = None) -> str:
    s = scope_of(scope)
    limit = max(1, min(int(limit or 30), 100))
    clauses = []
    if (name or "").strip():
        if not NAME_FILTER_RE.match(name.strip()):
            raise ToolError("The name filter may hold letters, digits, spaces, '.', '_', '/' and '-' only.")
        clauses.append(f'name ~ "{name.strip()}"')
    if s["repos"] != ALL:
        clauses.append("(" + " OR ".join(f'slug = "{r}"' for r in s["repos"]) + ")")
    query = {"pagelen": 50, "sort": "-updated_on"}
    if clauses:
        query["q"] = " AND ".join(clauses)
    readable = lambda r: s["repos"] == ALL or str(r.get("slug", "")).lower() in s["repos"]
    found, more = paged(f"/repositories/{_quote(config()['workspace'])}", query, limit, readable)
    if not found:
        return "0 repositories match. Try without a name filter, or tell the user none is assigned."
    lines = [f"{len(found)} repositor{'y' if len(found) == 1 else 'ies'}{' (more exist; raise limit)' if more else ''}:"
             " slug | project | language | updated | what you may do"]
    for r in found:
        slug = str(r.get("slug", "?")).lower()
        lines.append(" | ".join([slug, (r.get("project") or {}).get("key") or "-", r.get("language") or "-",
                                 (r.get("updated_on") or "")[:10] or "-", access_text(s, slug)]))
    return "\n".join(lines)


def commits(scope: str, repo: str, branch: str | None = None, limit: int | None = None) -> str:
    repo = check_repo(scope, repo)
    limit = max(1, min(int(limit or 15), 50))
    query = {"pagelen": min(limit, 50)}
    if (branch or "").strip():
        query["include"] = check_ref(branch)
    found, more = paged(repo_path(repo, "/commits"), query, limit)
    if not found:
        return f"0 commits in {repo}{' on ' + branch if branch else ''}."
    lines = [f"{len(found)} commit(s) in {repo}{' on ' + branch.strip() if branch else ''}, newest first"
             f"{' (more exist)' if more else ''}: hash | date | author | message"]
    for c in found:
        msg = (c.get("message") or "").strip().split("\n", 1)[0]
        lines.append(" | ".join([(c.get("hash") or "?")[:12], (c.get("date") or "")[:16].replace("T", " "),
                                 _author(c), _one_line(msg)]))
    return "\n".join(lines)


def pipelines(scope: str, repo: str, ref: str | None = None, build_number: int | None = None,
              step: str | None = None, grep: str | None = None, options: bool = False) -> str:
    repo = check_repo(scope, repo)
    ref = check_ref(ref) if (ref or "").strip() else None
    if build_number:
        return pipeline_detail(repo, int(build_number), step, grep)
    if step or grep:
        raise ToolError("step and grep need a build_number. Call bitbucket_pipelines without them to find it.")
    if options:
        return launchable(scope, repo, ref)
    return recent_runs(repo, ref)


def recent_runs(repo: str, ref: str | None, limit: int = 15) -> str:
    keep = (lambda p: ((p.get("target") or {}).get("ref_name") == ref)) if ref else (lambda p: True)
    found, _ = paged(repo_path(repo, "/pipelines/"), {"sort": "-created_on", "pagelen": 30}, limit, keep)
    if not found:
        return f"No pipeline runs in {repo}{' on ' + ref if ref else ''}. Use options=true to see what can be run."
    lines = [f"Last {len(found)} pipeline run(s) in {repo}{' on ' + ref if ref else ''}: "
             "#build | state | ref | selector | commit | duration | creator"]
    for p in found:
        lines.append(_run_line(p))
    return "\n".join(lines)


def _run_line(p: dict) -> str:
    t = p.get("target") or {}
    return " | ".join([f"#{p.get('build_number', '?')}", _state(p), t.get("ref_name") or "-", _selector(t),
                       ((t.get("commit") or {}).get("hash") or "-")[:7],
                       _duration(p.get("duration_in_seconds") or p.get("build_seconds_used")), _who(p.get("creator"))])


def _get_pipeline(repo: str, build_number: int) -> dict:
    p = request("GET", repo_path(repo, f"/pipelines/{int(build_number)}")) or {}
    if not p.get("uuid"):
        raise ToolError(f"No pipeline #{build_number} in {repo}. Use bitbucket_pipelines to see the recent runs.")
    return p


def pipeline_detail(repo: str, build_number: int, step: str | None, grep: str | None) -> str:
    p = _get_pipeline(repo, build_number)
    uuid = _quote(p["uuid"])
    steps = (request("GET", repo_path(repo, f"/pipelines/{uuid}/steps/"), query={"pagelen": 100}) or {}).get("values") or []
    url = f"{WEB}/{config()['workspace']}/{repo}/pipelines/results/{p.get('build_number', build_number)}"
    lines = [f"Pipeline {_run_line(p)}", f"URL: {url}",
             f"{len(steps)} step(s): index | name | state | duration"]
    for i, s in enumerate(steps, 1):
        lines.append(" | ".join([str(i), s.get("name") or "(unnamed)", _state(s),
                                 _duration(s.get("duration_in_seconds"))]))
    if not (step or "").strip():
        if grep:
            raise ToolError("grep needs a step. Give the step's index or name from this list.")
        lines.append("Give step (index or name) to read the end of that step's log.")
        return "\n".join(lines)
    chosen = _pick_step(steps, step)
    lines.append(_log_tail(repo, uuid, chosen, grep))
    return "\n".join(lines)


def _pick_step(steps: list, step: str) -> dict:
    want = str(step).strip()
    if want.isdigit() and 1 <= int(want) <= len(steps):
        return steps[int(want) - 1]
    low = want.lower()
    match = [s for s in steps if (s.get("name") or "").lower() == low] \
        or [s for s in steps if low in (s.get("name") or "").lower()]
    if len(match) != 1:
        names = ", ".join(f"{i} {s.get('name') or '(unnamed)'}" for i, s in enumerate(steps, 1)) or "none"
        raise ToolError(f"No single step matches \"{step}\". Steps: {names}. Use the index.")
    return match[0]


def _log_tail(repo: str, uuid: str, step: dict, grep: str | None) -> str:
    path = repo_path(repo, f"/pipelines/{uuid}/steps/{_quote(step.get('uuid') or '')}/log")
    try:
        text = request("GET", path, raw=True, headers={"Range": f"bytes=-{LOG_BYTES}"})
    except ToolError as e:
        if "HTTP 416" not in str(e):
            raise
        text = request("GET", path, raw=True)
    rows = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if grep:
        needle = grep.strip().lower()
        rows = [r for r in rows if needle in r.lower()]
    rows = [redact_log(r)[:300] for r in rows if r.strip()][-LOG_LINES:]
    head = f"Log of step \"{step.get('name') or '(unnamed)'}\", last {len(rows)} line(s)" \
           f"{' containing ' + json.dumps(grep.strip()) if grep else ''} (credentials shown as ***):"
    if not rows:
        return head + "\n(nothing" + (" matches; try another grep" if grep else " logged yet") + ")"
    # The newest lines matter most, so trim from the top to fit.
    budget, kept = MAX_OUTPUT - 1500, []
    for r in reversed(rows):
        budget -= len(r) + 1
        if budget < 0:
            break
        kept.append(r)
    return head + "\n" + "\n".join(reversed(kept))


# ---------------------------------------------------------------- what can be run

TOP_KINDS = ("default", "branches", "tags", "custom", "pull-requests")
KEY_LINE = re.compile(r"""^(?P<q>['"]?)(?P<key>[^'"#]+?)(?P=q)\s*:(?:\s.*)?$""")


def parse_pipelines_yml(text: str) -> dict:
    """The names under `pipelines:` in a bitbucket-pipelines.yml: {kind: [names]}; default is ["default"]."""
    out = {k: [] for k in TOP_KINDS}
    inside, kind, kind_indent, name_indent = False, None, None, None
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            inside, kind, kind_indent = stripped.split("#", 1)[0].strip() == "pipelines:", None, None
            continue
        if not inside or stripped.startswith("-"):
            continue
        m = KEY_LINE.match(stripped)
        if kind_indent is None or indent <= kind_indent:
            kind_indent = indent if kind_indent is None else kind_indent
            if indent == kind_indent:
                kind = m.group("key").strip() if m else None
                name_indent = None
                if kind == "default":
                    out["default"].append("default")
            continue
        if kind in ("branches", "tags", "custom", "pull-requests") and m:
            name_indent = indent if name_indent is None else name_indent
            if indent == name_indent:
                out[kind].append(m.group("key").strip())
    return out


def _default_branch(repo: str) -> str:
    r = request("GET", repo_path(repo)) or {}
    return (r.get("mainbranch") or {}).get("name") or "main"


def _commit_of(repo: str, ref: str) -> str:
    """The full hash of the newest commit on a branch."""
    found, _ = paged(repo_path(repo, "/commits"), {"include": ref, "pagelen": 1}, 1)
    if not found or not found[0].get("hash"):
        raise ToolError(f"No branch \"{ref}\" in {repo}. Use bitbucket_commits or ask the user for the branch.")
    return found[0]["hash"]


def launchable(scope: str, repo: str, ref: str | None) -> str:
    s = scope_of(scope)
    ref = ref or _default_branch(repo)
    commit = _commit_of(repo, ref)
    try:
        yml = request("GET", repo_path(repo, f"/src/{commit}/bitbucket-pipelines.yml"), raw=True)
    except ToolError as e:
        if "HTTP 404" not in str(e):
            raise
        yml = ""
    parsed = parse_pipelines_yml(yml)
    seen, _ = paged(repo_path(repo, "/pipelines/"), {"sort": "-created_on", "pagelen": 50}, 50)
    recent = []
    for p in seen:
        name = _custom_name(p.get("target"))
        if name and name not in parsed["custom"] and name not in recent:
            recent.append(name)

    def mark(name: str) -> str:
        return f"{name} ({'runs directly' if bypassed(s, repo, name) else 'after Approve'})"

    lines = [f"Pipelines of {repo} on {ref} ({commit[:7]}){'' if yml else ': no bitbucket-pipelines.yml there'}:"]
    lines.append("Custom, run with bitbucket_run_pipeline: " + (", ".join(mark(n) for n in parsed["custom"]) or "none"))
    if recent:
        lines.append("Custom, seen in recent runs but not in this file: " + ", ".join(mark(n) for n in recent))
    lines.append("Run by Bitbucket on push to branches: " + (", ".join(parsed["branches"]) or "none")
                 + ("; default for other branches" if parsed["default"] else ""))
    if parsed["tags"]:
        lines.append("Run on tags: " + ", ".join(parsed["tags"]))
    if parsed["pull-requests"]:
        lines.append("Run on pull requests from: " + ", ".join(parsed["pull-requests"]))
    return "\n".join(lines)


# ---------------------------------------------------------------- actions

def run_pipeline(scope: str, repo: str, ref: str, pipeline: str, commit: str | None = None) -> str:
    ref, pipeline = check_ref(ref), check_pipeline(pipeline)
    if commit and not HASH_RE.match(commit.strip()):
        raise ToolError(f"\"{commit}\" is not a commit hash. Use the hash bitbucket_commits shows, or leave it out.")
    repo = check_action(scope, repo, pipeline, f"Running {pipeline}")
    target = {"type": "pipeline_ref_target", "ref_type": "branch", "ref_name": ref,
              "selector": {"type": "custom", "pattern": pipeline}}
    if commit:
        full = commit.strip().lower()
        if len(full) < 40:
            full = (request("GET", repo_path(repo, f"/commit/{full}")) or {}).get("hash") or full
        target["commit"] = {"type": "commit", "hash": full}
    res = request("POST", repo_path(repo, "/pipelines/"), {"target": target}) or {}
    n = res.get("build_number", "?")
    return (f"Started pipeline #{n}: {pipeline} on {ref} in {repo}: {WEB}/{config()['workspace']}/{repo}/pipelines/"
            f"results/{n}. Follow it with bitbucket_pipelines build_number={n}. Do not call bitbucket_run_pipeline "
            "again for it.")


def stop_pipeline(scope: str, repo: str, build_number: int) -> str:
    repo = check_repo(scope, repo)
    if not APPROVED:
        s = scope_of(scope)
        if s["bypass"].get(repo) is None:  # nothing may be stopped directly here: ask before reading anything
            raise NeedsApproval(scope, repo, f"Stopping #{build_number}")
    p = _get_pipeline(repo, int(build_number))
    check_action(scope, repo, _custom_name(p.get("target")), f"Stopping #{build_number}")
    if (p.get("state") or {}).get("name") == "COMPLETED":
        return f"Pipeline #{build_number} in {repo} already finished ({_state(p)}). Nothing was stopped."
    request("POST", repo_path(repo, f"/pipelines/{_quote(p['uuid'])}/stopPipeline"))
    return (f"Asked Bitbucket to stop pipeline #{build_number} in {repo}. Check it with bitbucket_pipelines "
            f"build_number={build_number}; do not call bitbucket_stop_pipeline again for it.")


# ---------------------------------------------------------------- actions the user approves on the page

OPS = {
    "bitbucket_run_pipeline": lambda scope, a: run_pipeline(scope, a["repo"], a["ref"], a["pipeline"], a.get("commit")),
    "bitbucket_stop_pipeline": lambda scope, a: stop_pipeline(scope, a["repo"], int(a["build_number"])),
}


def _approvals():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "approvals"))
    import approvals  # noqa: E402  (a sibling module of this one, in the same Tanka checkout)
    return approvals


def _request_text(tool: str, args: dict) -> tuple[str, str]:
    repo = str(args.get("repo", "")).lower()
    if tool == "bitbucket_run_pipeline":
        what = f"run {args.get('pipeline', '')} on {args.get('ref', '')} in {repo}"
        if args.get("commit"):
            what += f" at {str(args['commit'])[:12]}"
    else:
        what = f"stop pipeline #{args.get('build_number', '')} in {repo}"
    text = "\n".join(f"{k}: {v}" for k, v in args.items() if v not in (None, ""))
    return f"Bitbucket: {what}", text


def approvals_scope(scope: str, approvals) -> str:
    """Where the page looks for this workspace's requests: its name, not the tools folder (approvals.workspace_scope
    falls back to the working directory, which for a tool is its tools/ folder)."""
    return approvals.workspace_scope() if os.environ.get("TANKA_WORKSPACE") else scope


def ask_approval(scope: str, repo: str, tool: str, args: dict) -> str:
    """Leave the action for the user's Approve click; nothing is sent to Bitbucket here."""
    if tool not in OPS:
        raise ToolError(f"Acting on {repo} needs the user's approval, and {tool or 'this tool'} cannot ask for it. "
                        "Tell the user.")
    ws = Path(os.environ.get("TANKA_WORKSPACE") or ".")
    if not (ws / ".claude" / "skills" / "approvals").is_dir():
        raise ToolError(f"Acting on {repo} needs the user's approval on the page, and this workspace has no approvals "
                        "module. Tell the user: tanka install approvals <workspace>. Nothing was run.")
    title, text = _request_text(tool, args)
    approvals = _approvals()
    try:
        a = approvals.ask(approvals_scope(scope, approvals), "bitbucket", title, text, {"tool": tool, "args": args})
    except approvals.ToolError as e:
        raise ToolError(str(e))
    return (f"Request {a['id']} saved: \"{a['title']}\". NOTHING was run in Bitbucket: it waits for the user's "
            "Approve click in the chat. Tell them, and do not say it started until they approve.")


def replay(scope: str, payload: dict) -> str:
    """The user's Approve click: run the stored action once (approve.py, a process of its own)."""
    global APPROVED
    tool, args = (payload or {}).get("tool"), (payload or {}).get("args")
    if tool not in OPS or not isinstance(args, dict):
        raise ToolError("This request is not a Bitbucket action this module made.")
    APPROVED = True
    try:
        return OPS[tool](scope, args)
    except (KeyError, TypeError, ValueError) as e:
        raise ToolError(f"The request is missing or has a bad value: {e}.")
    finally:
        APPROVED = False
