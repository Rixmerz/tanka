#!/usr/bin/env python3
"""memory_search: Searches the long-term memory of this workspace: facts, decisions with their reason, preferences, and people and project context stored earlier with memory_remember."""
import os
import sys
from pathlib import Path

# The memory module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "memory"))
import memory as m  # noqa: E402

# Memories belong to this scope only; other workspaces' memories do not exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    print(m.render_search(SCOPE, args))


m.run(main)
