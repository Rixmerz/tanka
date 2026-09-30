#!/usr/bin/env python3
"""gmail_inbox: list or search the conversations of a mailbox, numbered for gmail_read and gmail_reply."""
import os
import sys
from pathlib import Path

# The Gmail module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "gmail"))
import gmail  # noqa: E402

# Only mailboxes whose "workspace" is this scope exist for this tool (see accounts.json).
SCOPE = "__SCOPE__"


def main(args):
    gmail.list_messages(SCOPE, args.get("account"), args.get("search") or "", args["unread"], args["limit"])


gmail.run(main)
