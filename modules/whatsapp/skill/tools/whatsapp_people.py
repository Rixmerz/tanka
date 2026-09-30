#!/usr/bin/env python3
"""whatsapp_people: the contacts of this scope with the fields of their record, optionally filtered."""
import os
import sys
from pathlib import Path

# The WhatsApp module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "whatsapp"))
import wa  # noqa: E402

# Only contacts whose role belongs to this scope exist for this tool (see contacts.json).
SCOPE = "__SCOPE__"


def main(args):
    wa.list_people(SCOPE, args.get("where") or "", args.get("search") or "")


wa.run(main)
