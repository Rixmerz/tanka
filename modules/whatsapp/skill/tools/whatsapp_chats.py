#!/usr/bin/env python3
"""whatsapp_chats: the chats this assistant may read, or one contact's thread with its attachments."""
import os
import sys
from pathlib import Path

# The WhatsApp module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "whatsapp"))
import wa  # noqa: E402

# Only contacts whose role belongs to this scope exist for this tool (see contacts.json).
SCOPE = "__SCOPE__"


def main(args):
    if args.get("contact"):
        wa.read_thread(SCOPE, args["contact"], args["limit"], args["attachments"])
    else:
        wa.list_chats(SCOPE)


wa.run(main)
