"""Bitbucket on the page (docs/page.md): whether the module is configured, where the assistant may act without
asking, and the Autonomy switch for routines. Running or stopping a pipeline outside the bypass list is an approvals
request: the approvals module shows it with Approve and Dismiss, and only that click acts."""
from __future__ import annotations

import json
from pathlib import Path

import bitbucket as bb
from tanka_kit import ToolError


def installed(ws: Path) -> bool:
    return (ws / ".claude" / "skills" / "bitbucket").is_dir()


UNATTENDED_LABEL = "Routines and triggers may run the bypass pipelines of this workspace's Bitbucket repositories"


def _scope(scope: str) -> dict | None:
    try:
        return bb.scope_of(scope)
    except bb.ToolError:
        return None


def state(scope: str, ws: Path) -> dict:
    s = _scope(scope)
    configured = bool(bb.setting("TANKA_BITBUCKET_WORKSPACE", "workspace")) and s is not None
    bypass = [{"repo": r, "pipelines": "*" if p == bb.ALL else sorted(p)} for r, p in sorted((s or {}).get("bypass", {}).items())]
    autonomy = [{"key": "unattended_write", "label": UNATTENDED_LABEL, "on": s["unattended_write"]}] if bypass else []
    return {"configured": configured, "repos": bb.describe(s["repos"]) if s else "none", "bypass": bypass,
            "autonomy": autonomy}


def _autonomy(scope: str, ws: Path, b: dict) -> dict:
    """The user's switch, kept in scopes.json beside the other keys of this workspace's entry."""
    if b.get("key") != "unattended_write":
        raise ToolError("Unknown switch.")
    data = json.loads(bb.SCOPES.read_text(encoding="utf-8"))
    entry = (data.get("scopes") or {}).get(scope)
    if not isinstance(entry, dict) or not entry.get("bypass"):
        raise ToolError("This workspace has no bypass repository; that is set in scopes.json.")
    entry["unattended_write"] = b.get("on") is True
    tmp = bb.SCOPES.with_name(f".{bb.SCOPES.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(bb.SCOPES)
    return {"ok": True}


def hint(scope: str, ws: Path) -> str:
    return ("bitbucket_run_pipeline and bitbucket_stop_pipeline act at once only in the repositories the user listed "
            "as bypass; anywhere else they leave a request and the user presses Approve in the chat. Say which "
            "happened, and never call them again for a waiting request.")


ACTIONS = {"autonomy": _autonomy}
