#!/usr/bin/env python3
"""cauce_send: a prompt into one cauce session of this workspace's repositories, through tanka-link."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "cauce"))
import cauce_link as cl  # noqa: E402

# Only a session listed for this workspace can be named; anything else is refused before it is written.
SCOPE = "__SCOPE__"


def sender() -> str:
    """The assistant's own name, from its workspace's persona (a tool runs in its own folder, not the workspace)."""
    name = cl.kit.persona_name(Path(os.environ.get("TANKA_WORKSPACE") or Path.cwd()))
    return name if cl.SENDER_RE.fullmatch(name) else "assistant"


def main(args):
    print(cl.send_text(SCOPE, int(args["number"]), args["text"], sender()))


cl.run(main)
