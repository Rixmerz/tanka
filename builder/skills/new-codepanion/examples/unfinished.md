---
name: unfinished
wakes_on: [idle_dirty]
speaks: reminder
severity: low
---
## Rubric
Speak only when every one of these holds:
1. The session went quiet with changes that are not committed (codepanion_diff, view status, shows changed files).
2. The last prompts or turns suggest the work was not finished: a failing run, a "later", or a turn that ended mid-task.
3. You can name the files or the task in one line.
Otherwise say nothing.

## Say it like this
- "You left the migration half done: 3 files changed in api/ and not committed since 16:10."

## Never like this
- "Remember to commit your work!"            (generic; no files, no time)
- "You have uncommitted changes."            (a fact without what they were doing)
