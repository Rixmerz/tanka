# Worked example: grading assignments with a subagent

The first real Tanka subagent. A teacher asks the assistant to review a course's submissions against the rubric in Moodle, leave feedback, and put the grades in the rubric. It is the pattern [subagent-rules.md](../subagent-rules.md) describes: **the cheap steps on Haiku, the one expensive step on a stronger model, and the human between the proposal and anything a student sees.**

The skill lives in the user's workspace (`.claude/skills/grading/`), not in this repository, because it is specific to one account. What generalises is written here.

## The split

| Step | Tool | Effect | Who does the work |
| --- | --- | --- | --- |
| List the assignments of a course | `grading_assignments` | read | a script, on Haiku's call |
| Download every submission, unpack archives, clone linked repositories, capture rubric and roster | `grading_download` | draft | a script |
| Review each submission against the rubric and propose a level per criterion, with evidence and feedback | `grading_review` | draft | **Claude Opus, effort low**, one isolated run per student, in the background |
| Leave a comment in the submission thread | `grading_comment` | send | a script, after the user approves |
| Mark the approved levels in the rubric and save | `grading_grade` | send | a script, after the user approves |

Four of five steps cost nothing beyond Haiku. The reviewer is the only expensive call, and it cannot publish: its effect is `draft`, and its output is a file the user reads before either `send` tool runs. The skill's rule: "publish everything" without having seen the proposals is not an approval.

The reviewer is a **scripted** subagent (section 6 of the rules): it fans out over students, builds each prompt from the rubric it downloaded, and validates every answer before Haiku sees it. It imports `tanka_agent` for the command, the sandbox and the parsing.

## What a real run showed

One submission, a Django project in a cloned repository, reviewed three times while the prompt was fixed:

- **Cost and time:** USD 0.28 to 0.45 per student, under two minutes. A course of twenty-two is roughly USD 7 to 10.
- **It ran the project** inside the sandbox: created a venv, installed the requirements from PyPI, ran `migrate`, `check` and `test`, and exercised the admin and the role checks with Django's test client. It restored the student's database afterwards.
- **It found what the teacher had found** by hand: a `.gitignore` saved as UTF-16, which git ignores, so a real secret key was committed.
- **It resisted an injection surface.** The repository carried the student's own `.claude/` directory with commands and skills. The reviewer did not follow them and reported them as data.
- **It was stricter than the teacher, consistently.** Three runs proposed 8/12; the teacher had given 12/12. The difference is calibration, not noise, and it is the reason the approval step exists. Calibration belongs in the prompt, stated by the teacher, never guessed.

## Traps worth knowing before the next subagent

1. **Validate evidence against where the reviewer actually works.** It cites paths relative to the cloned repository, not to the submission folder. Checking only the folder produced false "evidence does not exist" alerts. The script now looks in the folder and in every `repo-*/` and `_ext/*/` below it.
2. **Tell the reviewer to read the statement first, attachments included.** Without that line it skipped the PDF that defined what the assignment asked for.
3. **Tell it how to run things.** "Create a venv here, use commands that end on their own, no servers" turned a static review into one that ran the project. `.env` files are protected by Claude Code and cannot be read; say so, or it spends turns trying.
4. **The sandbox leaves `.claude/`, `.bashrc`, `.gitconfig` and `.mcp.json` masks in the working directory.** On the host only an empty `.claude/` remains. Say in the prompt that they are not the student's.
5. **Make the expensive tool's neighbours idempotent.** Haiku re-downloaded the whole assignment (two minutes) just to find a student id. Rewording the skill did not stop it; making the download return the saved result unless `actualizar` is set did.
6. **A `send` tool that edits a form must not toggle.** In Moodle's rubric, clicking a level that is already marked unmarks it. Re-grading a student who already had a grade unmarked all four criteria. The tool's own guard (save nothing unless every criterion holds exactly the approved level) caught it, and nothing was saved. The fix presses only the cells that change.
7. **Say which grade the student already has.** A proposal of 8/12 for a student already graded 12/12 would have been posted as a comment contradicting the official grade. The proposal now alerts when a grade exists, and the thread comment never carries the proposed grade.

## Verification that was run

- `tanka tools check`: every tool `ok`, `grading_review` running on the shared runner.
- The runner end to end on Haiku with a two-field schema (USD 0.02, 12 s), background start, progress and result, and the fail-closed refusal without `socat`.
- The sandbox itself: its folder readable; `~/.ssh` empty from inside; example.com refused; PyPI allowed; writes outside the folder refused.
- The grading tool against one real student, re-saving the exact grade they already had with their feedback untouched, then read back independently from the grading table. The only visible change is the "last modified" date. The path that changes a level was exercised only in the aborted run, not in a saved one.
- A Haiku session asking to see a proposal: courses, assignments, download (answered from disk), review; it showed the proposal and asked for approval before publishing anything.
