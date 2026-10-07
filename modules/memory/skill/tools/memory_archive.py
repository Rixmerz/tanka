#!/usr/bin/env python3
"""memory_archive: Archives one memory of this workspace so memory_search no longer shows it, or restores an archived one."""
import os
import sys
from pathlib import Path

# The memory module ships with Tanka; TANKA_PLUGIN_DIR is <repo>/plugin for every tool.
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "memory"))
import memory as m  # noqa: E402

# Memories belong to this scope only; other workspaces' memories do not exist for this tool.
SCOPE = "__SCOPE__"


def main(args):
    r = m.set_archived(SCOPE, args["id"], not args.get("restore"))
    print(("Archived" if r["archived"] else "Restored") + f" memory {r['id']}:\n" + m.line(r))


m.run(main)
