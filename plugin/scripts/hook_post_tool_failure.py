#!/usr/bin/env python3
"""PostToolUseFailure: record the failure. From the second one on the same
tool, tell the model to stop and report instead of retrying blindly."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402


def main() -> None:
    inp = tc.read_input()
    root = tc.workspace_root(inp)
    tool = str(inp.get("tool_name", ""))
    n = 0
    try:
        st = tc.load_session(root, str(inp.get("session_id", "unknown")))
        tc.ensure_turn(st, inp.get("prompt_id"))
        tc.set_outcome(st, inp.get("tool_use_id"), tool, inp.get("tool_input", {}), "error")
        n = int(st["consecutive_failures"].get(tool, 0))
        tc.save_session(root, st)
    except Exception:
        pass
    policy = tc.load_policy(root)
    limit = int(policy["loop_guard"]["max_consecutive_failures"])
    err = str(inp.get("error", ""))[:300]
    if n >= limit - 1:
        msg = (f"{tool} has now failed {n} times in a row ({err}). DO NOT retry it with the same arguments. "
               "If there is no clear alternative, report to the user with `Status: blocked` and the literal error.")
    else:
        msg = f"{tool} failed: {err}. Read the error before retrying; if the cause is a missing detail, ask the user for it."
    tc.emit(tc.additional_context("PostToolUseFailure", msg))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
