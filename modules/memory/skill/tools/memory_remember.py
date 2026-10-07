#!/usr/bin/env python3
"""memory_remember: Stores one memory in the long-term memory of this workspace: a stable fact, a decision with its reason, a preference, or context about a person or project that the user stated or confirmed."""
import os
import sys
from pathlib import Path

# The memory module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "memory"))
import memory as m  # noqa: E402

# Memories belong to this scope only; other workspaces' memories do not exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    r = m.remember(SCOPE, args["text"], args.get("kind") or "note", args.get("tags") or "", args.get("source") or "")
    if r["existed"]:
        state = " (it is archived; memory_archive with restore true shows it again)" if r["archived"] else ""
        print(f"Already remembered as memory {r['id']}{state}. Nothing new stored.")
    else:
        print(f"Stored memory {r['id']}.")


m.run(main)
