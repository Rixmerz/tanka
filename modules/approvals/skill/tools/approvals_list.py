#!/usr/bin/env python3
"""approvals_list: the requests waiting for the user, and what became of the others."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "approvals"))
import approvals  # noqa: E402

SCOPE = "__SCOPE__"


def main(args):
    approvals.list_tool(SCOPE)


approvals.run(main)
