#!/usr/bin/env python3
"""routines_propose: Saves a routine proposal that waits for the user's approval."""
import os
import sys
from pathlib import Path

# The routines module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "routines"))
import routines as r  # noqa: E402

# Proposals belong to this scope only; other workspaces' proposals do not exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    r.propose_tool(SCOPE, r.workspace(SCOPE), args["name"], args["every"], args["task"], args.get("budget", r.DEFAULT_BUDGET), args.get("why", ""))


r.run(main)
