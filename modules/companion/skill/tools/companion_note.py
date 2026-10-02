#!/usr/bin/env python3
"""companion_note: Queues one remark for the user from one of your lenses: it appears in their status line and in their notes, and never interrupts the coding session."""
import os
import sys
from pathlib import Path

# The companion module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "companion"))
import companion as c  # noqa: E402

# Only sessions in projects that watch.json assigns to this scope exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    c.note(SCOPE, args["lens"], args["text"], args["evidence"], args.get("session"))


c.run(main)
