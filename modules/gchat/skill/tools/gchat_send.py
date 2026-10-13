#!/usr/bin/env python3
"""gchat_send: a message, optionally with a file, into one Google Chat conversation; by default only a request."""
import os
import sys
from pathlib import Path

# The module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "gchat"))
import gchat  # noqa: E402

# The Google account accounts.json gives this scope is the only one this tool writes from.
SCOPE = "__SCOPE__"


def main(args):
    return gchat.send(SCOPE, str(args["conversation"]), args.get("text") or "", args.get("attachment"))


gchat.run(main)
