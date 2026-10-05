#!/usr/bin/env python3
"""cauce_board: the coding tasks in the repositories this workspace may use."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "cauce"))
import cauce_link as cl  # noqa: E402

# Only this workspace's repositories exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    print(cl.board_text(SCOPE, args.get("repo")))


cl.run(main)
