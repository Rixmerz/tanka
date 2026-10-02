#!/usr/bin/env python3
"""desk_card: Adds one card for the user, or closes one: a check item or a reminder."""
import os
import sys
from pathlib import Path

# The desk module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "desk"))
import desk as d  # noqa: E402

# Cards belong to this scope only; other workspaces' cards do not exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    d.card_tool(SCOPE, args["kind"], args["text"], args.get("topic"), args.get("at"), bool(args.get("done")), args.get("evidence") or "")


d.run(main)
