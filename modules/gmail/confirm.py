#!/usr/bin/env python3
"""Run by the page when the user presses Delete or Keep on a conversation the assistant asked to move to the trash.

usage: confirm.py delete|dismiss <scope> <request id>"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gmail  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in ("delete", "dismiss"):
        print("usage: confirm.py delete|dismiss <scope> <request id>", file=sys.stderr)
        return 2
    try:
        print((gmail.confirm_trash if argv[0] == "delete" else gmail.dismiss_trash)(argv[1], argv[2]))
    except gmail.ToolError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
