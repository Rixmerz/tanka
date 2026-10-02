---
name: big-turn
wakes_on: [turn_end_substantial]
speaks: finding
severity: low
---
## Rubric
Speak only when every one of these holds:
1. companion_diff (view stat, then diff for the files that matter) shows the change the turn made.
2. You found one concrete problem in it: a bug, a deleted check, a secret, a test that no longer tests anything. Point at the file and the line.
3. The user's prompts do not already say they know about it.
Otherwise say nothing. Style, naming and "could be cleaner" are never enough.

## Say it like this
- "In api/retry.py the except now returns None instead of raising: callers that check for the exception will never see it."

## Never like this
- "Big change! Make sure to test it."              (no finding)
- "Consider adding more comments."                 (style, not a problem)
- "Looks good to me."                              (silence is the default; say nothing)
