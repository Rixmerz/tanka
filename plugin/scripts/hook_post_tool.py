#!/usr/bin/env python3
"""PostToolUse: mark the call as successful and clear the failure counter."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402


def main() -> None:
    inp = tc.read_input()
    root = tc.workspace_root(inp)
    tool = str(inp.get("tool_name", ""))
    try:
        st = tc.load_session(root, str(inp.get("session_id", "unknown")))
        tc.ensure_turn(st, inp.get("prompt_id"))
        tc.set_outcome(st, inp.get("tool_use_id"), tool, inp.get("tool_input", {}), "ok")
        tc.save_session(root, st)
    except Exception:
        pass
    policy = tc.load_policy(root)
    if tc.classify_tool(tool, policy) in ("send", "modify"):
        tc.emit(tc.additional_context("PostToolUse", f"{tool} succeeded. Report it to the user quoting the real tool result (id, time or recipient returned), not an assumption."))
    tc.emit({})


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
