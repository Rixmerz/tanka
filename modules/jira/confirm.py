#!/usr/bin/env python3
"""Run by the page when the user presses Delete or Keep on an issue the assistant asked to delete.

usage: confirm.py delete|dismiss <scope> <request id>

It is a separate process so the token_wrapper can inject the Jira token, as it does for the tools."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import jira  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[0] not in ("delete", "dismiss"):
        print("usage: confirm.py delete|dismiss <scope> <request id>", file=sys.stderr)
        return 2
    jira.relaunch_with_token()
    try:
        print((jira.confirm_delete if argv[0] == "delete" else jira.dismiss_delete)(argv[1], argv[2]))
    except jira.ToolError as e:
        print(jira.redact(str(e)), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
