#!/usr/bin/env python3
"""bitbucket_commits: recent commits of one repository, optionally on one branch."""
import os
import sys
from pathlib import Path

# The Bitbucket module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "bitbucket"))
import bitbucket  # noqa: E402

# Only the repositories scopes.json gives this scope exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    return bitbucket.commits(SCOPE, args["repo"], args.get("branch"), args.get("limit"))


bitbucket.run(main)
