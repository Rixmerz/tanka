#!/usr/bin/env python3
"""Run by the approvals module when the user presses Approve on a Jira change this assistant asked for.

It reads {"id", "payload"} on stdin and runs that one stored change, once; it is the only path by which a change in
a project under `write_on_approval` reaches Jira."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "jira"))
import jira  # noqa: E402

SCOPE = "__SCOPE__"

jira.relaunch_with_token()  # before stdin is read, so the relaunched process still gets it
try:
    print(jira.replay(SCOPE, json.load(sys.stdin).get("payload")))
except jira.ToolError as e:
    sys.exit(jira.redact(str(e)))
