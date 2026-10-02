#!/usr/bin/env python3
"""codepanion_digest: Shows what happened in one watched Claude Code session, oldest to newest: the user's prompts, the tools the coding agent ran with their targets, failures with their error, and where each turn ended."""
import os
import sys
from pathlib import Path

# The codepanion module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "codepanion"))
import codepanion as c  # noqa: E402

# Only sessions in projects that watch.json assigns to this scope exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    c.digest(SCOPE, args.get("session"), args.get("signal"), args["limit"])


c.run(main)
