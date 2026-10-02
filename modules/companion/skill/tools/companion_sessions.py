#!/usr/bin/env python3
"""companion_sessions: Lists the user's Claude Code sessions in the projects this companion watches, from the last 24 hours, newest first, and your last notes to the user."""
import os
import sys
from pathlib import Path

# The companion module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "companion"))
import companion as c  # noqa: E402

# Only sessions in projects that watch.json assigns to this scope exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    c.list_sessions(SCOPE)


c.run(main)
