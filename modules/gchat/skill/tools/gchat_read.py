#!/usr/bin/env python3
"""gchat_read: the Google Chat conversations, or the last messages of one."""
import os
import sys
from pathlib import Path

# The module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "gchat"))
import gchat  # noqa: E402

# The Google account accounts.json gives this scope is the only one this tool reads.
SCOPE = "__SCOPE__"


def main(args):
    if args.get("conversation"):
        return gchat.read(SCOPE, str(args["conversation"]), args.get("limit") or 20)
    return gchat.list_chats(SCOPE, args.get("query"), args.get("limit") or gchat.LIST_LIMIT)


gchat.run(main)
