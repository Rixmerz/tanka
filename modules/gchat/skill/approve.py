#!/usr/bin/env python3
"""Run by the approvals module when the user presses Approve on a Google Chat message this assistant asked to send.

It reads {"id", "payload"} on stdin and sends that one stored message, once."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "gchat"))
import gchat  # noqa: E402

SCOPE = "__SCOPE__"

try:
    print(gchat.replay(SCOPE, json.load(sys.stdin).get("payload")))
except gchat.ToolError as e:
    sys.exit(str(e))
