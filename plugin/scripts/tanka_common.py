#!/usr/bin/env python3
"""Shared helpers for the Tanka hooks.

Standard library only. Every hook reads a JSON event on stdin, consults the
workspace state under `.tanka/`, and writes a JSON decision on stdout. No hook
may raise: a broken harness must never take the session down, but it must not
open the door to risky actions either — fail closed for writes and for MCP
tools, fail open for reads.

All harness text is English. The assistant speaks whatever language the user
picked in `.tanka/persona.json`; the hooks tell it what to do, the model does
the talking.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

TANKA_DIR_NAME = ".tanka"
STATE_DIR_NAME = "state"
SESSION_STATE_DIR = "sessions"
OBJECTIVE_FILE = "objective.json"
PERSONA_FILE = "persona.json"
POLICY_FILE = "policy.json"
MCP_FILE = "mcp.json"

# --------------------------------------------------------------------------- #
# Default policy. The user overrides parts of it in .tanka/policy.json (deep
# merge). Anything not described here does not exist.
# --------------------------------------------------------------------------- #
DEFAULT_POLICY: dict = {
    "version": 1,
    # MCP tools are classified by name. Classes are tried in order and the
    # first pattern that matches wins; anything unmatched is "unknown".
    "tool_classes": {
        "destructive": [
            r"mcp__.*__(delete|trash|remove|purge|revoke|destroy|wipe|drop|clear)[a-z0-9_]*",
            r"mcp__.*__(mark_.*_spam|report_spam|block_|unsubscribe)[a-z0-9_]*",
        ],
        "send": [
            r"mcp__.*__(send|reply|forward|post|share|publish|submit|invite|notify|dispatch)[a-z0-9_]*",
            r"mcp__.*__(create|add)_(comment|reply|message|event|meeting)[a-z0-9_]*",
        ],
        "draft": [
            r"mcp__.*__(create_draft|update_draft|save_draft|draft)[a-z0-9_]*",
        ],
        "modify": [
            r"mcp__.*__(label|unlabel|mark|update|move|archive|create|copy|rename|edit|patch|put|set|apply|assign|upload|write|untrash|unmark|insert|append)[a-z0-9_]*",
        ],
        "read": [
            r"mcp__.*__(get|list|search|read|fetch|find|download|query|describe|show|view|count|check|lookup|preview|export|resolve|batch_get|batch_list)[a-z0-9_]*",
        ],
    },
    # Decision per class: allow | ask | deny
    "decisions": {
        "read": "allow",
        "draft": "allow",
        "modify": "ask",
        "send": "ask",
        "destructive": "deny",
        "unknown": "ask",
    },
    # Claude Code's built-in tools
    "builtin": {
        # Always denied: the role is assistant, not programmer.
        "deny": ["Bash", "PowerShell", "NotebookEdit", "REPL", "Computer"],
        # Paths (globs relative to the workspace) where Write/Edit are allowed.
        "write_allow_globs": [
            ".tanka/state/**",
            ".tanka/drafts/**",
            ".tanka/persona.json",
            ".tanka/objectives/**",
            "notes/**",
        ],
    },
    # Validation applied to the tool_input of "send"-class tools.
    "send_validation": {
        "recipient_fields": ["to", "cc", "bcc", "recipients", "recipient", "email", "emails", "attendees", "channel", "user", "users", "phone", "number"],
        "subject_fields": ["subject", "title"],
        "body_fields": ["body", "text", "message", "content", "html", "description", "comment", "note"],
        "require_subject": False,
        "min_body_chars": 15,
        "max_recipients": 8,
        "placeholder_patterns": [
            r"\[[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ _]{2,}\]",
            r"\{\{[^}]+\}\}",
            r"<(insert|inserta|nombre|name|fecha|date|placeholder)[^>]*>",
            r"\bTODO\b",
            r"\bTBD\b",
            r"\bXXX+\b",
            r"lorem ipsum",
        ],
        "secret_patterns": [
            r"sk-ant-[A-Za-z0-9_-]{10,}",
            r"AKIA[0-9A-Z]{16}",
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
            r"\bgh[pousr]_[A-Za-z0-9]{20,}\b",
            r"(?i)(password|contraseña)\s*[:=]\s*\S{6,}",
        ],
        # Empty = any recipient. If set, only these (regex).
        "recipient_allowlist": [],
        # Always blocked (regex matched against each recipient).
        "recipient_blocklist": [r"(?i)noreply@", r"(?i)no-reply@", r"(?i)@example\.com$"],
        # Domains considered internal; anything else is flagged in the
        # confirmation prompt as an external recipient.
        "internal_domains": [],
    },
    "loop_guard": {
        "max_identical_calls_per_turn": 2,
        "max_calls_per_turn": 25,
        "max_same_tool_per_turn": 12,
        "max_consecutive_failures": 3,
        "max_calls_per_session": 400,
    },
    "objective": {
        "require_closing_report_when_tools_used": True,
        # The canonical closing line is English ("Status: done"), but the check
        # also accepts the equivalent in a few other languages so that an
        # assistant working in the user's language is not blocked unfairly.
        "closing_report_regex": r"(?im)^\s*[*_`]*(status|estado|état|etat|stato|statut)[*_`]*\s*:[*_`\s]*(done|completed?|finished|partial|blocked|needs?[\s_-]*(confirmation|user|input)|completado|hecho|parcial|bloqueado|necesita[\s_-]*(confirmaci[oó]n|usuario|input)|termin[ée]|bloqu[ée])",
        # Phrases that claim an externally visible action was carried out.
        # English and Spanish ship by default; add your language here if the
        # assistant works in another one.
        "claim_patterns": {
            "send": [
                r"(?i)\b(i('ve| have)? |just )?(sent|forwarded|replied|posted|published|submitted)\b",
                r"(?i)\b(he |ya )?(enviad[oa]|envi[ée]|mand[ée]|reenviad[oa]|reenvi[ée]|publiqu[ée]|publicad[oa]|contest[ée]|respond[íi]|respondid[oa])\b",
            ],
            "modify": [
                r"(?i)\b(labeled|labelled|archived|moved to|marked as)\b",
                r"(?i)\b(etiquetad[oa]|etiquet[ée]|archivad[oa]|archiv[ée]|movid[oa]|mov[íi]|marcad[oa] como|marqu[ée])\b",
            ],
            "destructive": [
                r"(?i)\b(deleted|trashed|removed)\b",
                r"(?i)\b(eliminad[oa]|elimin[ée]|borrad[oa]|borr[ée])\b",
            ],
        },
    },
    # Hard rules re-injected on every turn. Short and imperative on purpose.
    "hard_rules": [
        "You are an assistant, not a programmer: you do not write or run code.",
        "Never claim you sent, deleted or changed anything without a tool result proving it.",
        "Before sending any message: show the full draft and wait for an explicit yes.",
        "If a detail is missing (recipient, date, subject, amount), ask. Do not invent it.",
        "If an action fails twice, stop and report. Do not repeat it.",
        "Close every task with a `Status: done | partial | blocked | needs-confirmation` line.",
    ],
}

# --------------------------------------------------------------------------- #
# Persona
# --------------------------------------------------------------------------- #
DEFAULT_PERSONA: dict = {
    "configured": False,
    "name": "Tanka",
    "user_name": "",
    "language": "",
    "tone": "",
    "personality": "",
    "output_format": "",
    "signature": "",
    "timezone": "",
    "notes": "",
}

# Used for behaviour when the user has not set the field yet. These are
# defaults, not answers: the field still counts as pending.
PERSONA_FALLBACKS: dict = {
    "tone": "warm, clear and professional",
    "personality": "Executive assistant: efficient, organised, discreet. Prefers asking over assuming.",
    "output_format": "Short answers. Lists for options. Drafts inside a quote block.",
}

# `language` is asked before anything else; everything here can be skipped at
# setup time and filled in later, when it first matters.
OPTIONAL_PERSONA_FIELDS = ["user_name", "tone", "personality", "output_format", "signature", "timezone", "notes"]

# Why each pending field matters, so the assistant can ask at the right moment
# instead of interrogating the user up front.
PERSONA_FIELD_HINTS: dict = {
    "user_name": "how to address the user",
    "tone": "how outgoing messages should read",
    "personality": "how you come across",
    "output_format": "how answers should be laid out",
    "signature": "needed before the first outgoing email is signed",
    "timezone": "needed before scheduling anything or reading a date",
    "notes": "priority people, recurring topics, things to avoid",
}


def persona_is_configured(persona: dict) -> bool:
    return bool(persona.get("configured")) and bool(str(persona.get("language", "")).strip())


def pending_persona_fields(persona: dict) -> list[str]:
    """Optional fields the user has not filled in yet."""
    return [f for f in OPTIONAL_PERSONA_FIELDS if not str(persona.get(f, "")).strip()]


def effective_persona(persona: dict) -> dict:
    """Persona with fallbacks applied, for rendering behaviour."""
    out = copy.deepcopy(persona)
    for k, v in PERSONA_FALLBACKS.items():
        if not str(out.get(k, "")).strip():
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
# I/O
# --------------------------------------------------------------------------- #
def read_input() -> dict:
    try:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def emit(obj: dict | None = None, exit_code: int = 0) -> None:
    if obj:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False))
    sys.stdout.flush()
    sys.exit(exit_code)


def block_with_stderr(reason: str) -> None:
    """Exit 2: the reason reaches Claude as a blocking error, on any event."""
    sys.stderr.write(reason)
    sys.stderr.flush()
    sys.exit(2)


def pre_tool_decision(decision: str, reason: str, extra: dict | None = None) -> None:
    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }
    if extra:
        out["hookSpecificOutput"].update(extra)
    emit(out)


def additional_context(event: str, text: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}


# --------------------------------------------------------------------------- #
# Paths and files
# --------------------------------------------------------------------------- #
def workspace_root(inp: dict | None = None) -> Path:
    env = os.environ.get("CLAUDE_PROJECT_DIR")
    if env:
        return Path(env)
    if inp and inp.get("cwd"):
        return Path(inp["cwd"])
    return Path.cwd()


def tanka_dir(root: Path) -> Path:
    return root / TANKA_DIR_NAME


def state_dir(root: Path) -> Path:
    d = tanka_dir(root) / STATE_DIR_NAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_json(path: Path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return copy.deepcopy(default)
    except Exception:
        return copy.deepcopy(default)


def save_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_policy(root: Path) -> dict:
    user = load_json(tanka_dir(root) / POLICY_FILE, {})
    return deep_merge(DEFAULT_POLICY, user if isinstance(user, dict) else {})


def load_persona(root: Path) -> dict:
    user = load_json(tanka_dir(root) / PERSONA_FILE, {})
    return deep_merge(DEFAULT_PERSONA, user if isinstance(user, dict) else {})


def objective_path(root: Path) -> Path:
    return state_dir(root) / OBJECTIVE_FILE


def load_objective(root: Path) -> dict | None:
    obj = load_json(objective_path(root), None)
    return obj if isinstance(obj, dict) else None


OBJECTIVE_REQUIRED = ["title", "goal", "done_when", "allowed_tool_classes", "status"]
OBJECTIVE_STATUSES = {"active", "done", "blocked", "needs_user", "cancelled"}
TOOL_CLASSES = {"read", "draft", "modify", "send", "destructive", "unknown"}


def validate_objective(obj) -> list[str]:
    """Return a list of problems; empty means the objective is valid."""
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["objective.json must be a JSON object"]
    for k in OBJECTIVE_REQUIRED:
        if k not in obj:
            errs.append(f"missing required field '{k}'")
    if "done_when" in obj and (not isinstance(obj["done_when"], list) or not obj["done_when"]):
        errs.append("'done_when' must be a non-empty list of checkable criteria")
    if "allowed_tool_classes" in obj:
        atc = obj["allowed_tool_classes"]
        if not isinstance(atc, list):
            errs.append("'allowed_tool_classes' must be a list")
        else:
            bad = [c for c in atc if c not in TOOL_CLASSES]
            if bad:
                errs.append(f"unknown tool classes: {bad}; valid ones: {sorted(TOOL_CLASSES)}")
            if "destructive" in atc:
                errs.append("'destructive' can never be authorised by an objective")
    if "status" in obj and obj["status"] not in OBJECTIVE_STATUSES:
        errs.append(f"invalid 'status'; valid ones: {sorted(OBJECTIVE_STATUSES)}")
    if "max_tool_calls" in obj:
        try:
            if int(obj["max_tool_calls"]) <= 0:
                errs.append("'max_tool_calls' must be greater than 0")
        except Exception:
            errs.append("'max_tool_calls' must be an integer")
    return errs


# --------------------------------------------------------------------------- #
# Session state (loop-guard counters)
# --------------------------------------------------------------------------- #
def _session_file(root: Path, session_id: str) -> Path:
    sid = re.sub(r"[^A-Za-z0-9_-]", "_", session_id or "unknown")[:80]
    return state_dir(root) / SESSION_STATE_DIR / f"{sid}.json"


def load_session(root: Path, session_id: str) -> dict:
    st = load_json(_session_file(root, session_id), {})
    st.setdefault("session_id", session_id)
    st.setdefault("created", time.time())
    st.setdefault("current_prompt_id", None)
    st.setdefault("turn", {"calls": [], "prompt_id": None})
    st.setdefault("session_calls", 0)
    st.setdefault("consecutive_failures", {})
    st.setdefault("denials", 0)
    return st


def save_session(root: Path, st: dict) -> None:
    st["updated"] = time.time()
    save_json(_session_file(root, st.get("session_id", "unknown")), st)


def ensure_turn(st: dict, prompt_id: str | None) -> None:
    """Reset the per-turn counters whenever prompt_id changes."""
    if prompt_id and st.get("turn", {}).get("prompt_id") != prompt_id:
        st["turn"] = {"calls": [], "prompt_id": prompt_id}
        st["current_prompt_id"] = prompt_id


def input_hash(tool_name: str, tool_input) -> str:
    try:
        canon = json.dumps(tool_input, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        canon = str(tool_input)
    return hashlib.sha256(f"{tool_name}\n{canon}".encode("utf-8")).hexdigest()[:16]


def record_call(st: dict, tool_name: str, tool_input, tool_class: str, outcome: str, tool_use_id: str | None = None) -> None:
    st["turn"]["calls"].append({
        "tool": tool_name,
        "class": tool_class,
        "hash": input_hash(tool_name, tool_input),
        "outcome": outcome,  # pending | ok | error | denied
        "id": tool_use_id,
        "t": time.time(),
    })
    st["session_calls"] = int(st.get("session_calls", 0)) + 1
    if outcome == "denied":
        st["denials"] = int(st.get("denials", 0)) + 1


def set_outcome(st: dict, tool_use_id: str | None, tool_name: str, tool_input, outcome: str) -> None:
    calls = st.get("turn", {}).get("calls", [])
    h = input_hash(tool_name, tool_input)
    for c in reversed(calls):
        if (tool_use_id and c.get("id") == tool_use_id) or (c.get("hash") == h and c.get("outcome") == "pending"):
            c["outcome"] = outcome
            break
    cf = st.setdefault("consecutive_failures", {})
    if outcome == "error":
        cf[tool_name] = int(cf.get(tool_name, 0)) + 1
    elif outcome == "ok":
        cf[tool_name] = 0


# --------------------------------------------------------------------------- #
# Tool classification
# --------------------------------------------------------------------------- #
def classify_tool(tool_name: str, policy: dict) -> str:
    if not tool_name.startswith("mcp__"):
        return "builtin"
    for cls in ("destructive", "send", "draft", "modify", "read"):
        for pat in policy["tool_classes"].get(cls, []):
            try:
                if re.fullmatch(pat, tool_name):
                    return cls
            except re.error:
                continue
    return "unknown"


def glob_to_regex(glob: str) -> str:
    out = ""
    i = 0
    while i < len(glob):
        c = glob[i]
        if glob[i:i + 3] == "**/":
            out += "(?:.*/)?"
            i += 3
            continue
        if glob[i:i + 2] == "**":
            out += ".*"
            i += 2
            continue
        if c == "*":
            out += "[^/]*"
        elif c == "?":
            out += "[^/]"
        else:
            out += re.escape(c)
        i += 1
    return "^" + out + "$"


def path_allowed(root: Path, file_path: str, globs: list[str]) -> bool:
    try:
        p = Path(file_path)
        if not p.is_absolute():
            p = root / p
        rel = os.path.relpath(os.path.normpath(str(p)), str(root))
    except Exception:
        return False
    if rel.startswith(".."):
        return False
    rel = rel.replace(os.sep, "/")
    return any(re.match(glob_to_regex(g), rel) for g in globs)


# --------------------------------------------------------------------------- #
# Outgoing-message validation
# --------------------------------------------------------------------------- #
def _flatten_strings(v, out: list[str], depth: int = 0) -> None:
    if depth > 6:
        return
    if isinstance(v, str):
        out.append(v)
    elif isinstance(v, (list, tuple)):
        for x in v:
            _flatten_strings(x, out, depth + 1)
    elif isinstance(v, dict):
        for x in v.values():
            _flatten_strings(x, out, depth + 1)


def _collect(tool_input: dict, fields: list[str]) -> list[str]:
    found: list[str] = []
    if not isinstance(tool_input, dict):
        return found
    for k, v in tool_input.items():
        if k.lower() in fields:
            _flatten_strings(v, found)
    # one nested level, e.g. {"message": {"to": ..., "body": ...}}
    for v in tool_input.values():
        if isinstance(v, dict):
            for k2, v2 in v.items():
                if k2.lower() in fields:
                    _flatten_strings(v2, found)
    return found


def split_recipients(values: list[str]) -> list[str]:
    out: list[str] = []
    for v in values:
        for part in re.split(r"[,;\n]+", v):
            part = part.strip()
            if part:
                out.append(part)
    return out


def _safe_search(pat: str, s: str) -> bool:
    try:
        return re.search(pat, s) is not None
    except re.error:
        return False


def validate_send(tool_input, policy: dict, objective: dict | None) -> tuple[list[str], dict]:
    """Return (violations, summary)."""
    sv = policy["send_validation"]
    violations: list[str] = []
    ti = tool_input if isinstance(tool_input, dict) else {}
    recipients = split_recipients(_collect(ti, [f.lower() for f in sv["recipient_fields"]]))
    subjects = _collect(ti, [f.lower() for f in sv["subject_fields"]])
    bodies = _collect(ti, [f.lower() for f in sv["body_fields"]])
    body = "\n".join(bodies)
    subject = " ".join(subjects)

    if sv.get("require_subject") and not subject.strip():
        violations.append("the subject is missing")
    if len(body.strip()) < int(sv.get("min_body_chars", 0)):
        violations.append(f"the body is shorter than {sv.get('min_body_chars')} characters")
    if recipients and len(recipients) > int(sv.get("max_recipients", 8)):
        violations.append(f"too many recipients ({len(recipients)} > {sv.get('max_recipients')})")

    haystack = subject + "\n" + body
    for pat in sv.get("placeholder_patterns", []):
        try:
            m = re.search(pat, haystack)
        except re.error:
            continue
        if m:
            violations.append(f"unfilled placeholder: '{m.group(0)}'")
            break
    for pat in sv.get("secret_patterns", []):
        try:
            if re.search(pat, haystack):
                violations.append("the content looks like it contains a secret or credential")
                break
        except re.error:
            continue

    allow = sv.get("recipient_allowlist") or []
    block = sv.get("recipient_blocklist") or []
    for r in recipients:
        for pat in block:
            try:
                if re.search(pat, r):
                    violations.append(f"recipient blocked by policy: {r}")
                    break
            except re.error:
                continue
        if allow and not any(_safe_search(p, r) for p in allow):
            violations.append(f"recipient outside the allowlist: {r}")

    if objective:
        if objective.get("may_send") is False:
            violations.append("the active objective forbids sending (may_send=false)")
        obj_allow = objective.get("recipient_allowlist") or []
        for r in recipients:
            if obj_allow and not any(_safe_search(p, r) for p in obj_allow):
                violations.append(f"recipient not authorised by the objective: {r}")

    external: list[str] = []
    internal = sv.get("internal_domains") or []
    if internal:
        for r in recipients:
            m = re.search(r"@([A-Za-z0-9.-]+)", r)
            if m and not any(m.group(1).lower().endswith(d.lower()) for d in internal):
                external.append(r)

    summary = {
        "recipients": recipients,
        "subject": subject[:120],
        "body_preview": re.sub(r"\s+", " ", body)[:240],
        "body_chars": len(body),
        "external_recipients": external,
    }
    return violations, summary


def fmt_send_summary(summary: dict) -> str:
    parts = []
    if summary.get("recipients"):
        parts.append("To: " + ", ".join(summary["recipients"]))
    if summary.get("subject"):
        parts.append("Subject: " + summary["subject"])
    parts.append(f"Body ({summary.get('body_chars', 0)} chars): {summary.get('body_preview', '')}")
    if summary.get("external_recipients"):
        parts.append("WARNING external recipients: " + ", ".join(summary["external_recipients"]))
    return "\n".join(parts)
