#!/usr/bin/env python3
"""gmail_trash: ask the user to move a conversation to the trash."""
import os
import sys
from pathlib import Path

# The Gmail module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "gmail"))
import gmail  # noqa: E402

# Only mailboxes whose "workspace" is this scope exist for this tool (see accounts.json).
SCOPE = "__SCOPE__"


def main(args):
    gmail.trash_tool(SCOPE, args["number"])


gmail.run(main)
