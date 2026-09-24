---
name: ticket
description: Implement one ticket from a ticket set end to end — orientation, test-first implementation, the local gate, adversarial self-review, and the PR or escalation.
---

# Implementing one ticket

An implementing agent is rewarded for a green build, and the cheapest way to
turn a red build green is to stop the tests reporting the problem. In unattended
runs, CI and the integrity gate are the only things between an agent and the
default branch. That is why the conventions here are strict, and why a green
build made green by weakening or editing a test is worse than a red one.

Work **one ticket**. Not two, not a ticket and a half. If you finish early, stop
and report; do not wander into the next ticket.

---

## 1. Orient before touching anything

### Repository and branch housekeeping

Before selecting or starting work on a ticket:

**Bash:**
```bash
git checkout master  # or main
git pull origin master
git fetch --prune
# Clean up local branches merged into the default branch:
git branch --merged master | grep -v '^\*\|master\|main' | xargs -r git branch -d
```

**PowerShell:**
```powershell
git checkout master  # or main
git pull origin master
git fetch --prune
# Clean up local branches merged into the default branch:
git branch --merged master | Where-Object { $_ -notmatch '^\*|\bmaster\b|\bmain\b' } | ForEach-Object { git branch -d $_.Trim() }
```

**If `git pull` says default branch has no tracked branch (shell-neutral):**
```
git branch --set-upstream-to=origin/master master
git remote set-head origin -a
```

### Orientation steps

Read, in this order:

1. **`AGENTS.md`** — the whole file: invariants, layering, conventions, and the
   active ticket plan.
2. **The ticket file** under `.scratch/<effort>/issues/NN-*.md`. Its `Blocked by:`
   line and its acceptance criteria are the contract.
3. **Every ADR the ticket references**, in `docs/adr/`. They are binding, not
   background. A ticket that names an ADR expects you to have read and followed it.
4. **`CONTEXT.md`** — the domain glossary. Use its words exactly. If a term you
   need is missing or the code contradicts it, flag it; do not silently pick a side.
5. **The module docstring of every file you are about to edit.** Docstrings explain
   why (`WHY THIS EXISTS`).

### Work the frontier

Never start a ticket whose `Blocked by:` names an unfinished ticket (a ticket
whose status is not `done`). If the only unblocked ticket is `ready-for-developer`
(a bench task, hardware task, or human judgement call), say so and stop. Do not
claim it, do not simulate it, do not build around it.

### Refer to roles, never to people

Never write real people's names, Slack IDs, or emails in code, tickets, commit
messages, or documentation. Refer to **roles** ("the approver", "a buyer", "the
developer"), never to people. (See ADR 0002).

### Claim the ticket

- Create a ticket branch: `ticket/<effort>-<NN>-<slug>`. One ticket, one branch.
  Never commit directly to the default branch (`master` / `main`).
- Set the ticket's `Status:` line to `in-progress` and commit that first before
  writing any code.

---

## 2. Decide whether to split into parallel agents (optional)

Judge by the seam, not by size.
- **Split when pieces do not share a seam**: independent unblocked tickets on
  separate branches, or implementation and tests written independently (one agent
  implements from the ticket, a second writes tests from acceptance criteria without
  reading the implementation; then reconcile).
- **Do not split**: a single vertical slice across layers, pieces sharing a test
  seam or fixtures, or small tasks where coordination overhead exceeds the work.

State your split decision and reasoning in one line before acting on it.

---

## 3. Implement, test first

Write the tests **from the ticket's acceptance criteria and the spec**, before
the implementation. Then run them and **watch them fail**. A test you have never
seen fail is not known to work.

### Architecture rules

- **Cores are pure.** Cores do no I/O (no network, no subprocess, no file writes,
  no clock reads). All I/O lives in thin adapters.
- **Every tunable has one home**: the repository's configuration, never hardcoded
  literals in logic or loops.
- **Never swallow an exception.** Handle errors explicitly or let them bubble with
  proper context.
- **Docstrings explain WHY**, with a `WHY THIS EXISTS` section recording the bug,
  constraint, ADR, or spec requirement that forced the design.
- Use `logging.getLogger(__name__)`. Never `print()` in library or application code.

### Checkpoint as you go (quota safety for local workers)

At each acceptance-criterion boundary, once that criterion's tests pass:
- Commit and push the work in progress to the ticket branch.
- Keep one progress note under the ticket's `## Comments`, of five lines or
  fewer, and overwrite it each time.

---

## 4. Run the full local gate

Run the gate commands specified in the repo's engine config (or `AGENTS.md`):
- Run linter / formatting checks (e.g. `ruff check .`).
- Run the tests-first verification gate (e.g. `python scripts/check_tests_first.py`).
- Run type checkers or ratchets if configured.
- Run the test suite (e.g. `pytest`).

### Strict test-first and zero-failure enforcement

- **100% pass before push.** Confirm **zero** failures before pushing any branch or
  opening a PR.
- **Never mute a failing test.** No `skip`, `skipif` or `xfail`, no deleted or
  weakened assertions, no loosened tolerances, no narrowed inputs, no `try/except`
  swallowing test errors. If you cannot make a test pass honestly, escalate.

---

## 5. Review your own diff, adversarially

Before opening a PR or landing, review your changes as if hunting for the reason
this will be reverted:
- **Tick each acceptance criterion individually.** If one cannot be ticked, the
  ticket is not done.
- **Break the implementation on purpose** for at least one criterion (flip a
  comparison, invert logic, drop a check) and confirm a test goes red. Then restore it.
- **Would a dishonest PR slip through?** Ensure tests verify actual behavior, not
  just call counts or private implementation details.
- **Does anything print or leak a secret, prompt, or denylist entry?** Check for
  `print()`, prompt logging, or echoing sensitive data.
- **Did any pure core gain I/O?**
- **Stale docstrings?** Update reasoning wherever behavior changed.
- **Line endings.** If `git status` shows unintended file modifications, check line
  endings (`.gitattributes`).

---

## 6. If you are a Jules worker

This section applies when executing as a Jules cloud worker:

- **Do not push.** Jules pushes changes automatically.
- **Do not open or edit PRs.** Do not run `gh pr create` or edit PR descriptions;
  the Jules session is configured with `automationMode: AUTO_CREATE_PR` and creates
  the pull request automatically.
- **CI is watched for you.** The engine dispatcher monitors the pull request and
  CI checks.
- **Respond to red CI by fixing.** If CI fails, you will receive the failing
  output. Diagnose the root cause, fix the implementation or test harness
  honestly, and commit.
- **Mark the ticket done in the same PR.** As your last step, in the ticket file:
  set `Status: done`, tick every criterion you implemented and verified (`- [x]`),
  and replace the progress note under `## Comments` with a dated summary of what
  was built and which tests cover which criterion. The integrity gate fails a PR
  that does not change its ticket file this way.
- **Budget: up to three fix attempts.** If red CI persists after three attempts,
  or if tests cannot pass without muting or weakening them, escalate immediately.
- **Escalation brief format.** When escalating, set the ticket's `Status:` to
  `blocked`, and write an escalation brief under `## Comments` in the exact
  format:
  ```markdown
  ## Escalation — <YYYY-MM-DD>
  Ticket: <NN> <title>   Branch: <branch>
  Goal: <one sentence from "What to build">
  Attempt 1: <what you tried> → <result>
  Attempt 2: <what you tried> → <result>
  Attempt 3: <what you tried> → <result>
  Failing output (exact, trimmed to the relevant lines):
  ```
  <paste exact output>
  ```
  Decision needed: <the one question a human or stronger model must answer>
  ```
  This brief is written so the developer can paste it directly into a stronger model.

---

## 6b. If you are a local (agy) worker

This section applies when executing as a local Antigravity CLI worker:

- **Checkpoint at every acceptance-criterion boundary.** Once each criterion's
  tests pass, commit and push the work in progress to the ticket branch, then
  overwrite the progress note under the ticket's `## Comments` (five lines or
  fewer):
  ```
  Progress (YYYY-MM-DD HH:MM): criteria 1–N done, tests green.
  Next: criterion M — <brief description>.
  ```
- **Do not push or open PRs yourself.** The local worker orchestrator monitors
  your output and handles quota errors.
- **Quota may pause you mid-ticket.** The orchestrator will resume you with
  `agy --continue`. If that is not possible, it will start a fresh session
  that includes your last progress note. Your next session should read that
  note (under `## Comments`) and continue from "Next:".

---

## 7. Landing or escalating (for local / human workers)

When running locally (outside of Jules):

### Landing the ticket
1. In the ticket file: set `Status: done`, tick every criterion (`- [x]`), and
   replace the progress note with a dated summary under `## Comments` (what was
   built, which tests cover which criterion, anything verified).
2. Commit code, tests, and the ticket file together on the ticket branch. Never
   commit directly to the default branch.
3. Push the branch: `git push -u origin <branch-name>`.
4. Create the Pull Request:
   - If GitHub CLI (`gh`) is available and authenticated:
     ```bash
     gh pr create --base master --head <branch-name> --title "<ticket title>" --body "<summary, criteria checklist, gate results>"
     ```
   - If `gh` is unavailable:
     Provide the direct GitHub compare URL (`https://github.com/<owner>/<repo>/pull/new/<branch-name>`)
     and output the structured PR body ready to paste.
5. Watch CI:
   - If `gh` is available: `gh pr checks --watch --fail-fast`.
   - If `gh` is not available, state plainly that CI was not observed.
   - If CI goes red: diagnose the root cause, fix and push again (up to two
     fix-and-push cycles).
   - If it still fails, escalate.

### Escalating
1. Commit finished work to the ticket branch.
2. Set the ticket's `Status:` to `blocked`.
3. Under `## Comments`, write the escalation brief using the exact format from
   Section 6.
4. If a PR exists and `gh` is available, convert it to draft (`gh pr ready --undo`);
   otherwise convert it via the GitHub web UI.
5. Stop and report.

---

## Never

- Start a second ticket in the same session.
- Claim `ready-for-developer` tickets or simulate bench/manual work.
- Commit to the default branch or merge your own PR.
- Mute, skip, or weaken a test.
- Call live APIs from tests or store real credentials in fixtures.
- Write real people's names, Slack IDs, or emails anywhere. Use roles.
