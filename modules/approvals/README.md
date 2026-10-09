# Approvals

Approve on the page what a workspace skill asks to do. A tool that must never act on its own (open a ticket in
another team's queue, publish, pay) only leaves a request with the exact text; the chat shows it under the answer
with **Approve** and **Dismiss**, and only your click runs it, once. The assistant has no tool to approve.

Tanka runs its tools without asking (`tanka dev` checked them), and a skill's "ask the user first" is a rule the
model follows. This module turns that rule into a button for the actions where following it is not enough.

## Setup

```bash
tanka install approvals <workspace>     # approvals_list, and the Approve buttons on the page
```

## Using it from a workspace skill

The tool that asks:

```python
import os, sys
from pathlib import Path
sys.path.insert(0, str(Path(os.environ["TANKA_PLUGIN_DIR"]).parent / "modules" / "approvals"))
import approvals

def main(args):
    payload = {"title": args["title"], "description": args["description"]}
    print(approvals.ask_tool(Path.cwd().name, "<skill>", args["title"], args["description"], payload))
```

`<workspace>/.claude/skills/<skill>/approve.py`, run by the Approve click with `{"id", "payload"}` on stdin. It
prints the result (shown on the page) and exits non-zero on failure:

```python
import json, sys
payload = json.load(sys.stdin)["payload"]
# ... do the one action with exactly this payload ...
print("Created EXAMPLE-123: https://example.com/EXAMPLE-123")
```

Write `approve.py` to check its payload: it is the only code that acts.

## How it works

- Requests live in `~/.tanka/shared/approvals/<scope>.json`, outside every workspace. Pending ones are kept until
  you act; the last 30 closed ones stay for the page.
- Approve marks the request `running` before it starts, so a second click or a second page cannot run it twice.
  It ends `approved` (with what `approve.py` printed) or `failed` (with its error). A failed request is not
  retried: if it timed out, check by hand whether it happened before asking again.
- The same title from the same skill cannot wait twice.

## Configuration

| Variable | Default | What |
|---|---|---|
| `TANKA_APPROVALS_HOME` | `~/.tanka/shared/approvals` | Where the requests are kept |
| `TANKA_APPROVALS_TIMEOUT` | `180` | Seconds `approve.py` may take |
