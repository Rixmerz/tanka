#!/usr/bin/env python3
"""cauce_sessions: the cauce sessions in this workspace's repositories, numbered for cauce_send."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "cauce"))
import cauce_link as cl  # noqa: E402

# Only this workspace's repositories: a session elsewhere does not exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    print(cl.sessions_text(SCOPE, args.get("repo")))


cl.run(main)
