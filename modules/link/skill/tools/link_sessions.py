#!/usr/bin/env python3
"""link_sessions: the user's open Claude Code sessions."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "link"))
import link  # noqa: E402

SCOPE = "__SCOPE__"


def main(args):
    link.sessions_tool(SCOPE)


link.run(main)
