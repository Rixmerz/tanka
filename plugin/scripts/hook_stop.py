#!/usr/bin/env python3
"""Stop: closure verification.

1. Unbacked claims: if the final message says "sent" / "deleted" / "labeled"
   but no successful tool result of that class exists in this turn, closure is
   blocked once and the model has to correct the message.
2. Closing report: with an active objective and tools used in the turn, the
   message must carry a `Status: ...` line.
Never blocks twice in a row (stop_hook_active)."""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tanka_common as tc  # noqa: E402

# A claim inside a negation, a condition or a question is not a claim.
NEGATION = re.compile(
    r"(?i)(\bno\b|\bnot\b|n't\b|\bnunca\b|\bsin\b|\bsi\b|\bif\b|\bantes de\b|\bbefore\b|\bpuedo\b|\bpodr[ií]a\b"
    r"|\bcan\b|\bcould\b|\bquieres\b|\bwant\b|\bwould\b|\bready\b|\blist[oa] para\b|\bcuando\b|\bwhen\b|\bonce\b|¿|\?)"
)
# A successful call of these classes is evidence for a claim of the given class.
SATISFIED_BY = {"send": {"send"}, "modify": {"modify", "draft", "send"}, "destructive": {"destructive"}}


def main() -> None:
    inp = tc.read_input()
    if inp.get("stop_hook_active"):
        tc.emit({})
        return
    root = tc.workspace_root(inp)
    policy = tc.load_policy(root)
    objective = tc.load_objective(root)
    msg = str(inp.get("last_assistant_message", "") or "")
    try:
        st = tc.load_session(root, str(inp.get("session_id", "unknown")))
        tc.ensure_turn(st, inp.get("prompt_id"))
        calls = st["turn"]["calls"]
    except Exception:
        calls = []

    ok_classes = {c["class"] for c in calls if c.get("outcome") == "ok"}
    problems: list[str] = []

    # 1) Claims without evidence
    for cls, pats in policy["objective"].get("claim_patterns", {}).items():
        if ok_classes & SATISFIED_BY.get(cls, {cls}):
            continue
        for pat in pats:
            try:
                m = re.search(pat, msg)
            except re.error:
                continue
            if not m:
                continue
            start = max(msg.rfind(".", 0, m.start()), msg.rfind("\n", 0, m.start()), msg.rfind("!", 0, m.start())) + 1
            before = msg[start:m.start()]
            after = msg[m.end():m.end() + 40]
            if NEGATION.search(before) or "?" in after.split("\n")[0]:
                continue
            problems.append(f"Your message claims a '{cls}' action (\"{m.group(0)}\") but this turn has no successful tool result of that class. "
                            "Fix the message: say exactly what was and was not done, or carry the action out (with confirmation) before claiming it.")
            break

    # 2) Closing report while an objective is active
    if objective and objective.get("status") == "active" and policy["objective"].get("require_closing_report_when_tools_used"):
        used = [c for c in calls if c.get("outcome") in ("ok", "error")]
        if used and not re.search(policy["objective"]["closing_report_regex"], msg):
            problems.append("An objective is active and you used tools this turn, but the message has no closing line. "
                            "Add at the end: `Status: done | partial | blocked | needs-confirmation`, then one line for what is done and what is left. "
                            "Write the word `Status` even when you are working in another language. "
                            "If the objective is complete, update .tanka/state/objective.json with status=done.")

    if problems:
        tc.block_with_stderr("Tanka blocked this closure:\n- " + "\n- ".join(problems))
    tc.emit({})


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
