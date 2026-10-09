#!/usr/bin/env python3
"""calendar_create: create one event without guests."""
import os
import sys
from pathlib import Path

# The calendar module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "calendar"))
import gcal  # noqa: E402

# Only accounts whose "workspace" is this scope exist for this tool (see accounts.json).
SCOPE = "__SCOPE__"


def main(args):
    gcal.create(SCOPE, args["title"], args["start"], args["end"], args.get("place") or "", args.get("description") or "", args["allow_overlap"])


gcal.run(main)
