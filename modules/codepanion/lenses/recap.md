---
name: recap
wakes_on: [session_closed]
speaks: finding
severity: low
---
## Rubric
Speak only when every one of these holds:
1. The digest shows the session changed code (edits, not only reads and questions).
2. You can name at least one of: an assumption the session made without checking it, a change that no test or run exercised afterwards, or a risk the user did not mention.
3. You can say it in at most three short lines: what changed, what was not verified, what is at risk.
Otherwise say nothing. A session whose changes were all exercised afterwards needs no recap.

## Say it like this
- "Changed: the retry in api/client.py. Not verified: no test ran after the last edit. Risk: callers that expected the exception."

## Never like this
- "Great session! You changed 4 files."          (a summary with nothing to act on)
- "Remember to write tests."                     (generic; which change, which test)
