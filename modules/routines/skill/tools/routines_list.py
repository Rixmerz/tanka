#!/usr/bin/env python3
"""routines_list: Lists the workspace's routines, triggers and pending proposals."""
import os
import sys
from pathlib import Path

# The routines module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "routines"))
import routines as r  # noqa: E402

# Proposals belong to this scope only; other workspaces' proposals do not exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    r.list_tool(SCOPE, r.workspace(SCOPE))


r.run(main)
