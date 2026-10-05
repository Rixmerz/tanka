#!/usr/bin/env python3
"""cauce_queue: queue a coding task in one of this workspace's repositories."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "cauce"))
import cauce_link as cl  # noqa: E402

# Only this workspace's repositories can be named; anything else is refused before cauce runs.
SCOPE = "__SCOPE__"


def main(args):
    t = cl.queue(SCOPE, args["repo"], args["task"], args.get("check"))
    print(f"Queued #{t['id']} in {t['repo_name']}: {cl.clip(t.get('title'), cl.TITLE_CHARS)}. "
          "It runs when the user starts the queue on their page (Code tab).")


cl.run(main)
