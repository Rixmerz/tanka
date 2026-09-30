#!/usr/bin/env python3
"""gmail_read: one message of the last list in full, with its attachments saved in the workspace."""
import os
import sys
from pathlib import Path

# The Gmail module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "gmail"))
import gmail  # noqa: E402

# Only mailboxes whose "workspace" is this scope exist for this tool (see accounts.json).
SCOPE = "__SCOPE__"


def main(args):
    gmail.read_message(SCOPE, args["number"])


gmail.run(main)
