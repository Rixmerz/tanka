#!/usr/bin/env python3
"""whatsapp_reply: send one text message to a contact whose role allows replies."""
import os
import sys
from pathlib import Path

# The WhatsApp module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "whatsapp"))
import wa  # noqa: E402

# Only contacts whose role belongs to this scope exist for this tool (see contacts.json).
SCOPE = "__SCOPE__"


def main(args):
    wa.reply(SCOPE, args["contact"], args["message"])


wa.run(main)
