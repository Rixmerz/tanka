"""What module libraries and their tools share: errors the model can act on, JSON on disk, secret
scrubbing, the named workspaces, and the page's chat file (docs/page.md).

Every module imports from here, so a ToolError raised in one is the same class in all of them.
Standard library only.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import tanka_common as tc  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
SHARED = Path.home() / ".tanka" / "shared"
WORKSPACES = Path(os.environ.get("TANKA_WORKSPACES", Path.home() / ".tanka" / "workspaces"))
PAGE_HOME = Path(os.environ.get("TANKA_PAGE_HOME", SHARED / "page"))
SCOPE_RE = re.compile(r"[a-z0-9][a-z0-9_-]*")

# The harness's own secret patterns, plus the forms that show up in sessions and messages.
SECRET_PATTERNS = [re.compile(p) for p in tc.DEFAULT_POLICY["send_validation"]["secret_patterns"] + [
    r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}",
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}",
    r"\bAIza[0-9A-Za-z_-]{35}\b",
    r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
    r"(?i)\b(api[_-]?key|secret|token|passwd|pwd)\b\s*[:=]\s*['\"]?[^\s'\"]{8,}",
    r"(?<=://)[^/\s:@]+:[^/\s@]+(?=@)",
    r"https://hooks\.slack\.com/services/\S+",
    r"\$2[aby]\$\d\d\$[./A-Za-z0-9]{53}",
]]


class ToolError(Exception):
    """A failure the model should relay: the message says what to do next."""


def run(main) -> None:
    """Entry point of every tool script: arguments as JSON on stdin, errors as one sentence."""
    try:
        main(json.load(sys.stdin))
    except ToolError as e:
        sys.exit(str(e))


def scrub(text) -> str:
    s = str(text or "")
    for pat in SECRET_PATTERNS:
        s = pat.sub("[secret]", s)
    return s


def clip(text, n: int) -> str:
    s = " ".join(scrub(text).split())
    return s if len(s) <= n else s[: n - 1] + "…"


def tail_clip(text, n: int) -> str:
    """Like clip, but keeps the end: an error's last lines say what went wrong."""
    s = " ".join(scrub(text).split())
    return s if len(s) <= n else "…" + s[-(n - 1):]


def now() -> float:
    return time.time()


def hhmm(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%H:%M")


def day_time(t: float) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M")


def today_start(t: float) -> float:
    return datetime.fromtimestamp(t).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except json.JSONDecodeError as e:
        raise ToolError(f"{path} is not valid JSON (line {e.lineno}). Tell the user; it must be fixed by hand.")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict]:
    """Every object in a JSON-lines file; a torn or foreign line is skipped, a missing file is empty."""
    out = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                out.append(obj)
    except FileNotFoundError:
        pass
    return out


def safe_id(s) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "", str(s or ""))[:64] or "unknown"


# ---------------------------------------------------------------- the named workspaces

def ws_dir(scope: str) -> Path:
    return (WORKSPACES / scope).resolve()


def scopes() -> list[str]:
    """Every named workspace (a folder in WORKSPACES with a Tanka policy)."""
    if not WORKSPACES.is_dir():
        return []
    return sorted(d.name for d in WORKSPACES.iterdir()
                  if SCOPE_RE.fullmatch(d.name) and (d / ".tanka" / "policy.json").is_file())


def persona_name(ws: Path) -> str:
    """What the user calls this assistant: its persona's name, or the workspace's."""
    try:
        name = json.loads((Path(ws) / ".tanka" / "persona.json").read_text(encoding="utf-8")).get("name", "")
    except (OSError, ValueError, AttributeError):
        name = ""
    return str(name).strip()[:40] or Path(ws).name


def persona_language(ws: Path) -> str:
    try:
        lang = json.loads((Path(ws) / ".tanka" / "persona.json").read_text(encoding="utf-8")).get("language")
    except (OSError, ValueError, AttributeError):
        lang = None
    return str(lang or "en").split("-")[0].lower()


# ---------------------------------------------------------------- the page's chat (docs/page.md)

def chat_file(scope: str) -> Path:
    """The page's chat with one workspace. Modules write what they say on their own here too (a
    fired reminder, the daily brief), so it stays in the conversation."""
    return PAGE_HOME / "chat" / f"{safe_id(scope)}.jsonl"


def chat_event(scope: str, event: dict) -> None:
    chat_file(scope).parent.mkdir(parents=True, exist_ok=True)
    with chat_file(scope).open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")


def adopt(old: Path, new: Path) -> bool:
    """Move data left at an old location to its new one, once: only when the new one does not exist."""
    if old.exists() and not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        old.rename(new)
        return True
    return False
