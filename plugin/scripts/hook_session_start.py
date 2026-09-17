#!/usr/bin/env python3
"""SessionStart: load persona, policy and objective, drive first-run setup, and
warn about configuration that would degrade a small model."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402

MAX_RECOMMENDED_SERVERS = 3

FIRST_RUN_BLOCK = """FIRST RUN — the assistant profile is not set up yet.
Before you do anything the user asked for, ask ONE short question: which language should you work in?
Ask it in English, and tell them they can simply reply in the language they want — their reply is the answer.
Then write that language into `.tanka/persona.json` (`language`, e.g. "en", "es", "pt-BR") together with `"configured": true`, confirm in that language in one line, and carry on with whatever they originally asked for.
Everything else about the profile (name, tone, signature, timezone…) is optional and can be skipped now and filled in later, one field at a time, when it first matters. Say so in that same line. Offer `/tanka:setup` for the user who wants to set it all at once."""


def main() -> None:
    inp = tc.read_input()
    root = tc.workspace_root(inp)
    source = inp.get("source", "startup")
    persona_raw = tc.load_persona(root)
    persona = tc.effective_persona(persona_raw)
    policy = tc.load_policy(root)
    objective = tc.load_objective(root)
    warnings: list[str] = []

    tdir = tc.tanka_dir(root)
    if not tdir.exists():
        warnings.append("There is no .tanka/ directory here, so this is not a Tanka workspace. Run `tanka init`.")
    for name in (tc.POLICY_FILE, tc.PERSONA_FILE, tc.MCP_FILE):
        p = tdir / name
        if p.exists():
            try:
                json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                warnings.append(f"{name} is not valid JSON ({exc}); defaults are being used instead.")
    mcp = tc.load_json(tdir / tc.MCP_FILE, {})
    servers = list((mcp.get("mcpServers") or {}).keys()) if isinstance(mcp, dict) else []
    if len(servers) > MAX_RECOMMENDED_SERVERS:
        warnings.append(f"{len(servers)} MCP servers are enabled ({', '.join(servers)}). Tool selection degrades above roughly 10-15 tools on a small model; keep only what this task needs.")

    lines: list[str] = []

    if not tc.persona_is_configured(persona_raw):
        lines.append(FIRST_RUN_BLOCK)
        lines.append("")
    else:
        who = f" to {persona['user_name']}" if persona.get("user_name") else ""
        lines.append(f"Session started ({source}). You are \"{persona['name']}\", personal assistant{who}.")
        lines.append(f"Work in this language: {persona['language']}. Everything you write to the user goes in that language, including drafts, unless the user asks otherwise for a specific message.")
        lines.append(f"Personality: {persona['personality']}")
        lines.append(f"Tone: {persona['tone']}. Format: {persona['output_format']}")
        if persona.get("signature"):
            lines.append(f"Email signature: {persona['signature']}")
        if persona.get("timezone"):
            lines.append(f"User timezone: {persona['timezone']}")
        if persona.get("notes"):
            lines.append(f"User notes: {persona['notes']}")

        pending = tc.pending_persona_fields(persona_raw)
        if pending:
            hints = ", ".join(f"{f} ({tc.PERSONA_FIELD_HINTS.get(f, '')})" for f in pending)
            lines.append("Profile fields still unset: " + hints + ". Do not interrogate the user about them. "
                         "Ask for one only at the moment it actually matters, in a single line, and offer to save it to .tanka/persona.json. "
                         "`/tanka:setup` fills them all in one pass.")

    lines.append("Enabled MCP servers: " + (", ".join(servers) if servers else "none (conversation and workspace files only)."))
    lines.append("Policy: read=" + policy["decisions"]["read"] + ", drafts=" + policy["decisions"]["draft"]
                 + ", modifications=" + policy["decisions"]["modify"] + ", sending=" + policy["decisions"]["send"]
                 + ", destructive=" + policy["decisions"]["destructive"] + ".")
    if objective:
        lines.append(f"Stored objective: \"{objective.get('title')}\" (status={objective.get('status')}). "
                     + ("Continue with it or close it with /tanka:plan close." if objective.get("status") == "active" else "It is closed; start a new one with /tanka:plan if needed."))
    if source == "compact":
        lines.append("The context was just compacted: re-read .tanka/state/objective.json before continuing, and do not repeat actions already carried out.")
    lines.append("Skills: /tanka:setup (profile), /tanka:plan (objective), /tanka:draft (email and messages), /tanka:triage (classify), /tanka:status (state).")
    if warnings:
        lines.append("WARNINGS: " + " | ".join(warnings))
    tc.emit(tc.additional_context("SessionStart", "\n".join(lines)))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
