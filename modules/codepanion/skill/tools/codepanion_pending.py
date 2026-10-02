#!/usr/bin/env python3
"""codepanion_pending: Lists the user's cards in this codepanion: reminders with their time, and check cards with the pending items of each topic."""
import os
import sys
from pathlib import Path

# The codepanion module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "codepanion"))
import codepanion as c  # noqa: E402

# Cards belong to this scope only; other workspaces' cards do not exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    c.list_pending(SCOPE, args.get("topic"))


c.run(main)
