#!/usr/bin/env python3
"""cauce_memory: problems and the fixes tried on them, in this workspace's repositories."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "cauce"))
import cauce_link as cl  # noqa: E402

# Only this workspace's repositories: another workspace's problems do not exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    print(cl.memory_text(SCOPE, args.get("query") or ""))


cl.run(main)
