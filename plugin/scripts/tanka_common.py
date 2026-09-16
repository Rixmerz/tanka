#!/usr/bin/env python3
"""Utilidades compartidas por los hooks de Tanka.

Solo biblioteca estándar. Cada hook lee JSON por stdin, consulta el estado del
workspace (.tanka/) y escribe JSON por stdout. Ningún hook debe lanzar
excepciones no controladas: un fallo del harness nunca debe tumbar la sesión,
pero tampoco debe abrir la puerta a acciones peligrosas (fail-closed para
tools de escritura, fail-open para lectura).
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
# Política por defecto. El usuario la sobreescribe parcialmente en
# .tanka/policy.json (deep merge). Todo lo que no esté aquí no existe.
# --------------------------------------------------------------------------- #
DEFAULT_POLICY: dict = {
    "version": 1,
    # Clasificación de tools MCP por nombre. Se evalúa en orden; la primera
    # clase cuyo patrón coincide gana. "unknown" si nada coincide.
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
    # Decisión por clase: allow | ask | deny
    "decisions": {
        "read": "allow",
        "draft": "allow",
        "modify": "ask",
        "send": "ask",
        "destructive": "deny",
        "unknown": "ask",
    },
    # Tools built-in de Claude Code
    "builtin": {
        # Siempre denegados (el rol es asistente, no programador).
        "deny": ["Bash", "PowerShell", "NotebookEdit", "REPL", "Computer"],
        # Rutas (glob relativos al workspace) donde Write/Edit están permitidos.
        "write_allow_globs": [
            ".tanka/state/**",
            ".tanka/drafts/**",
            ".tanka/persona.json",
            ".tanka/objectives/**",
            "notes/**",
        ],
    },
    # Validaciones aplicadas al tool_input de tools clase "send".
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
        # Vacío = cualquier destinatario. Si se define, solo estos (regex).
        "recipient_allowlist": [],
        # Siempre bloqueados (regex sobre cada destinatario).
        "recipient_blocklist": [r"(?i)noreply@", r"(?i)no-reply@", r"(?i)@example\.com$"],
        # Dominios "externos" que fuerzan aviso en la razón de confirmación.
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
        "closing_report_regex": r"(?im)^\s*[*_`]*(estado|status)[*_`]*\s*:[*_`\s]*(completado|done|hecho|bloqueado|blocked|parcial|partial|necesita[ _]?(confirmaci[oó]n|usuario|input)|needs[ _]?(user|confirmation|input))",
        # Frases que afirman haber realizado una acción con efectos externos.
        "claim_patterns": {
            "send": [
                r"(?i)\b(he |ya )?(enviad[oa]|envié|mandé|reenviad[oa]|reenvié|publiqué|publicad[oa]|contesté|respondí|respondid[oa])\b",
                r"(?i)\b(i('ve| have)? |just )?(sent|forwarded|replied|posted|published|submitted)\b",
            ],
            "modify": [
                r"(?i)\b(etiquetad[oa]|etiqueté|archivad[oa]|archivé|movid[oa]|moví|marcad[oa] como|marqué)\b",
                r"(?i)\b(labeled|labelled|archived|moved to|marked as)\b",
            ],
            "destructive": [
                r"(?i)\b(eliminad[oa]|eliminé|borrad[oa]|borré|deleted|trashed|removed)\b",
            ],
        },
    },
    # Reglas duras reinyectadas en cada turno (cortas, imperativas).
    "hard_rules": [
        "Eres asistente, no programador: no escribes ni ejecutas código.",
        "Nunca afirmes haber enviado, borrado o modificado algo sin un tool_result que lo confirme.",
        "Antes de enviar cualquier mensaje: muestra el borrador completo y espera un 'sí' explícito del usuario.",
        "Si falta un dato (destinatario, fecha, asunto, importe), pregunta; no lo inventes.",
        "Si una acción falla dos veces, para y reporta; no la repitas.",
        "Termina cada tarea con una línea `Estado: completado | parcial | bloqueado | necesita confirmación`.",
    ],
}

DEFAULT_PERSONA: dict = {
    "name": "Tanka",
    "user_name": "",
    "language": "es",
    "tone": "cercano, claro y profesional",
    "personality": "Asistente ejecutivo: eficiente, ordenado, discreto. Prefiere preguntar a suponer.",
    "output_format": "Respuestas cortas. Listas para opciones. Borradores en bloque de cita.",
    "signature": "",
    "timezone": "",
    "notes": "",
}


# --------------------------------------------------------------------------- #
# E/S
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
    """Exit 2: el motivo va a Claude como error bloqueante (todos los eventos)."""
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
# Rutas y ficheros
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
    """Devuelve lista de errores (vacía si es válido)."""
    errs: list[str] = []
    if not isinstance(obj, dict):
        return ["objective.json debe ser un objeto JSON"]
    for k in OBJECTIVE_REQUIRED:
        if k not in obj:
            errs.append(f"falta el campo obligatorio '{k}'")
    if "done_when" in obj and (not isinstance(obj["done_when"], list) or not obj["done_when"]):
        errs.append("'done_when' debe ser una lista no vacía de criterios verificables")
    if "allowed_tool_classes" in obj:
        atc = obj["allowed_tool_classes"]
        if not isinstance(atc, list):
            errs.append("'allowed_tool_classes' debe ser una lista")
        else:
            bad = [c for c in atc if c not in TOOL_CLASSES]
            if bad:
                errs.append(f"clases de tool desconocidas: {bad}; válidas: {sorted(TOOL_CLASSES)}")
            if "destructive" in atc:
                errs.append("'destructive' no puede autorizarse desde un objetivo")
    if "status" in obj and obj["status"] not in OBJECTIVE_STATUSES:
        errs.append(f"'status' inválido; válidos: {sorted(OBJECTIVE_STATUSES)}")
    if "max_tool_calls" in obj:
        try:
            if int(obj["max_tool_calls"]) <= 0:
                errs.append("'max_tool_calls' debe ser > 0")
        except Exception:
            errs.append("'max_tool_calls' debe ser un entero")
    return errs


# --------------------------------------------------------------------------- #
# Estado de sesión (contadores anti-loop)
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
    """Resetea contadores por turno cuando cambia prompt_id."""
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
# Clasificación de tools
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
# Validación de envíos
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
    # un nivel anidado (p.ej. {"message": {"to": ..., "body": ...}})
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


def validate_send(tool_input, policy: dict, objective: dict | None) -> tuple[list[str], dict]:
    """Devuelve (violaciones, resumen)."""
    sv = policy["send_validation"]
    violations: list[str] = []
    ti = tool_input if isinstance(tool_input, dict) else {}
    recipients = split_recipients(_collect(ti, [f.lower() for f in sv["recipient_fields"]]))
    subjects = _collect(ti, [f.lower() for f in sv["subject_fields"]])
    bodies = _collect(ti, [f.lower() for f in sv["body_fields"]])
    body = "\n".join(bodies)
    subject = " ".join(subjects)

    if sv.get("require_subject") and not subject.strip():
        violations.append("falta el asunto")
    if len(body.strip()) < int(sv.get("min_body_chars", 0)):
        violations.append(f"el cuerpo tiene menos de {sv.get('min_body_chars')} caracteres")
    if recipients and len(recipients) > int(sv.get("max_recipients", 8)):
        violations.append(f"demasiados destinatarios ({len(recipients)} > {sv.get('max_recipients')})")

    haystack = subject + "\n" + body
    for pat in sv.get("placeholder_patterns", []):
        try:
            m = re.search(pat, haystack)
        except re.error:
            continue
        if m:
            violations.append(f"placeholder sin rellenar: '{m.group(0)}'")
            break
    for pat in sv.get("secret_patterns", []):
        try:
            if re.search(pat, haystack):
                violations.append("el contenido parece incluir un secreto/credencial")
                break
        except re.error:
            continue

    allow = sv.get("recipient_allowlist") or []
    block = sv.get("recipient_blocklist") or []
    for r in recipients:
        for pat in block:
            try:
                if re.search(pat, r):
                    violations.append(f"destinatario bloqueado por política: {r}")
                    break
            except re.error:
                continue
        if allow and not any(_safe_search(p, r) for p in allow):
            violations.append(f"destinatario fuera de la allowlist: {r}")

    if objective:
        if objective.get("may_send") is False:
            violations.append("el objetivo activo prohíbe enviar (may_send=false)")
        obj_allow = objective.get("recipient_allowlist") or []
        for r in recipients:
            if obj_allow and not any(_safe_search(p, r) for p in obj_allow):
                violations.append(f"destinatario no autorizado por el objetivo: {r}")

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


def _safe_search(pat: str, s: str) -> bool:
    try:
        return re.search(pat, s) is not None
    except re.error:
        return False


def fmt_send_summary(summary: dict) -> str:
    parts = []
    if summary.get("recipients"):
        parts.append("Para: " + ", ".join(summary["recipients"]))
    if summary.get("subject"):
        parts.append("Asunto: " + summary["subject"])
    parts.append(f"Cuerpo ({summary.get('body_chars', 0)} chars): {summary.get('body_preview', '')}")
    if summary.get("external_recipients"):
        parts.append("⚠ Destinatarios externos: " + ", ".join(summary["external_recipients"]))
    return "\n".join(parts)
