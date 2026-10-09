#!/usr/bin/env python3
"""calendar_delete: ask the user to delete an event."""
import os
import sys
from pathlib import Path

# The calendar module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "calendar"))
import gcal  # noqa: E402

# Only accounts whose "workspace" is this scope exist for this tool (see accounts.json).
SCOPE = "__SCOPE__"


def main(args):
    gcal.delete_tool(SCOPE, args["number"])


gcal.run(main)
