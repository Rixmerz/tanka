#!/usr/bin/env python3
"""boards_rows: Lists the user's boards, or the rows of one."""
import os
import sys
from pathlib import Path

# The boards module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "boards"))
import boards as b  # noqa: E402

# Boards belong to this scope only; other workspaces' boards do not exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    b.rows_tool(SCOPE, args.get("view"), args.get("match"))


b.run(main)
