#!/usr/bin/env python3
"""jira_create: create one issue."""
import os
import sys
from pathlib import Path

# The Jira module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "jira"))
import jira  # noqa: E402

# Only the projects scopes.json gives this scope exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    return jira.create(SCOPE, args["project"], args["type"], args["summary"], args.get("description"), args.get("labels"), args.get("parent"))


jira.run(main)
