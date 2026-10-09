#!/usr/bin/env python3
"""jira_delete: ask the user to delete an issue."""
import os
import sys
from pathlib import Path

# The Jira module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "jira"))
import jira  # noqa: E402

# Only the projects scopes.json gives this scope exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    return jira.delete_tool(SCOPE, args["key"])


jira.run(main)
