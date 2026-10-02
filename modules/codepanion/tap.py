#!/usr/bin/env python3
"""The codepanion's tap: one hook command in the user's normal Claude Code, observe-only.

It appends one compact event per hook to the codepanion feed, only for projects in
watch.json, with secrets scrubbed. It never prints, never decides, never blocks:
any error is swallowed, because a broken tap must never break the user's session.
"""
import json
import sys

try:
    sys.path.insert(0, __file__.rsplit("/", 1)[0])
    import codepanion

    codepanion.tap(json.load(sys.stdin))
except Exception:  # noqa: BLE001 - observe-only: nothing may reach the user's session
    pass
sys.exit(0)
