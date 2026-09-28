#!/usr/bin/env python3
"""The Tanka MCP server (stdio, JSON-RPC). Standard library only.

Exposes the workspace's tool manifests (.claude/skills/<skill>/tools/*.json)
as MCP tools. The launcher starts it with the workspace path as its only
argument. Manifests are re-read on every tools/list and tools/call, so a tool
fixed with `tanka dev` is picked up by the next session without reinstalling.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_tools as tt  # noqa: E402

INSTRUCTIONS = (
    "These are the assistant's only tools for acting outside the conversation. "
    "Every tool belongs to the skill named by its prefix (library_* belongs to the library skill): "
    "read that skill before the first call. If no tool covers the request, say so; do not improvise."
)


def tool_entry(m: dict) -> dict:
    return {
        "name": m["name"],
        "description": tt.compiled_description(m),
        "inputSchema": tt.input_schema(m),
        "annotations": {
            "readOnlyHint": m["effect"] == "read",
            "destructiveHint": False,
            "openWorldHint": m["effect"] == "send",
        },
    }


def handle(ws: Path, req: dict):
    method = req.get("method")
    params = req.get("params") or {}
    if method == "initialize":
        return {"protocolVersion": params.get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": tt.SERVER_NAME, "version": "1.0"},
                "instructions": INSTRUCTIONS}
    if method == "tools/list":
        tools, problems = tt.scan(ws)
        for p in problems:
            print(f"tanka-mcp: skipped: {p}", file=sys.stderr)
        return {"tools": [tool_entry(m) for _, m in sorted(tools.items())]}
    if method == "tools/call":
        tools, _ = tt.scan(ws)
        m = tools.get(params.get("name"))
        if not m:
            text, err = f"There is no tool named {params.get('name')}. Use only the tools listed.", True
        else:
            text, err = tt.run_tool(ws, m, params.get("arguments") or {})
        return {"content": [{"type": "text", "text": text}], "isError": err}
    if method == "ping":
        return {}
    raise LookupError(method)


def main() -> None:
    ws = Path(sys.argv[1] if len(sys.argv) > 1 else os.getcwd()).resolve()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = req.get("id")
        if rid is None:
            continue  # notification
        try:
            msg = {"jsonrpc": "2.0", "id": rid, "result": handle(ws, req)}
        except LookupError:
            msg = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {req.get('method')}"}}
        except Exception as exc:  # never kill the session over one bad request
            msg = {"jsonrpc": "2.0", "id": rid, "error": {"code": -32603, "message": str(exc)}}
        sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
