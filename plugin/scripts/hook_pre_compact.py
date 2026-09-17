#!/usr/bin/env python3
"""PreCompact: ask the summary to preserve the objective, pending confirmations
and actions already carried out, so nothing gets sent twice after compaction."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402


def main() -> None:
    inp = tc.read_input()
    root = tc.workspace_root(inp)
    objective = tc.load_objective(root)
    text = ("When summarising, keep verbatim: (1) the active objective and its done-criteria, "
            "(2) the list of actions ALREADY carried out with their results (ids, recipients, times), "
            "(3) drafts waiting for confirmation, and (4) open questions to the user.")
    if objective:
        text += f" Objective: \"{objective.get('title')}\" status={objective.get('status')}."
    tc.emit(tc.additional_context("PreCompact", text))


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
