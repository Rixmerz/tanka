#!/usr/bin/env python3
"""companion_diff: Reads the git state of the project a watched session works in, without changing anything and without running the repository's own programs."""
import os
import sys
from pathlib import Path

# The companion module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "companion"))
import companion as c  # noqa: E402

# Only sessions in projects that watch.json assigns to this scope exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    c.diff(SCOPE, args.get("session"), args["view"], args["max_lines"])


c.run(main)
