#!/usr/bin/env python3
"""codepanion_card: Adds one card for the user, or closes one: a check item or a reminder."""
import os
import sys
from pathlib import Path

# The codepanion module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "codepanion"))
import codepanion as c  # noqa: E402

# Cards belong to this scope only; other workspaces' cards do not exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    c.card_tool(SCOPE, args["kind"], args["text"], args.get("topic"), args.get("at"), bool(args.get("done")), args.get("evidence") or "")


c.run(main)
