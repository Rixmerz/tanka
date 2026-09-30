#!/usr/bin/env python3
"""whatsapp_contacts: find numbers in the phone's address book and show the role of each."""
import os
import sys
from pathlib import Path

# The WhatsApp module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "whatsapp"))
import wa  # noqa: E402

# Only contacts whose role belongs to this scope exist for this tool (see contacts.json).
SCOPE = "__SCOPE__"


def main(args):
    wa.search_contacts(SCOPE, args["query"])


wa.run(main)
