#!/usr/bin/env python3
"""cauce_task: one coding task, its attempts and what it needs."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "cauce"))
import cauce_link as cl  # noqa: E402

# A task outside this workspace's repositories is refused.
SCOPE = "__SCOPE__"


def main(args):
    print(cl.task_text(SCOPE, int(args["task_id"])))


cl.run(main)
