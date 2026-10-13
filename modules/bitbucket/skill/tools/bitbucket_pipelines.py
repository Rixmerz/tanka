#!/usr/bin/env python3
"""bitbucket_pipelines: recent pipeline runs, one run's steps and log, or what can be run."""
import os
import sys
from pathlib import Path

# The Bitbucket module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "bitbucket"))
import bitbucket  # noqa: E402

# Only the repositories scopes.json gives this scope exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    return bitbucket.pipelines(SCOPE, args["repo"], args.get("ref"), args.get("build_number"), args.get("step"),
                               args.get("grep"), bool(args.get("options")))


bitbucket.run(main)
