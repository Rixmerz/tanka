#!/usr/bin/env python3
"""Run by the approvals module when the user presses Approve on a Bitbucket action this assistant asked for.

It reads {"id", "payload"} on stdin and runs that one stored action (run or stop a pipeline), once, through the module's
signed-in browser session; it is the only path by which an action outside the user's bypass list reaches Bitbucket."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "bitbucket"))
import bitbucket  # noqa: E402

SCOPE = "__SCOPE__"

try:
    print(bitbucket.replay(SCOPE, json.load(sys.stdin).get("payload")))
except bitbucket.ToolError as e:
    sys.exit(str(e))
