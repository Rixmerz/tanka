#!/usr/bin/env python3
"""Run by the page when the user presses Delete or Keep on an event the assistant asked to delete.

usage: confirm.py delete|dismiss <scope> <request id>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gcal  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in ("delete", "dismiss"):
        print("usage: confirm.py delete|dismiss <scope> <request id>", file=sys.stderr)
        return 2
    try:
        print((gcal.confirm_delete if argv[0] == "delete" else gcal.dismiss_delete)(argv[1], argv[2]))
    except gcal.ToolError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
