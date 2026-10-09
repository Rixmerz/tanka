#!/usr/bin/env python3
"""link_send: send a prompt into one open Claude Code session."""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "link"))
import link  # noqa: E402

SCOPE = "__SCOPE__"


def sender() -> str:
    """The assistant's own name, from its persona."""
    try:
        name = json.loads((Path.cwd() / ".tanka" / "persona.json").read_text(encoding="utf-8")).get("name") or "assistant"
    except (OSError, ValueError):
        name = "assistant"
    return name if link.SENDERS_RE.fullmatch(name) else "assistant"


def main(args):
    link.send_tool(SCOPE, args["number"], args["text"], sender())


link.run(main)
