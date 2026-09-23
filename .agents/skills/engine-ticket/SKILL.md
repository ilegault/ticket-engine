---
name: engine-ticket
description: Implement one ticket from the ticket-engine repo's own ticket sets (.scratch/<effort>/issues/NN-*.md) end to end — orientation, test-first implementation, the local gate, adversarial self-review, and the PR. Use whenever asked to implement, work, pick up or continue an engine ticket, or "work the frontier" in the ticket-engine repo.
---

# Implementing one ticket-engine ticket

ticket-engine is the machinery that will later merge other repos' PRs **with no
human watching**. A bug here does not break one feature; it lets a dishonest test
suite merge into lab code, or burns a night of quota. That is why the rules below
are strict, and why a green build made green by weakening a test is worse than a
red one.

Work **one ticket**. When it is done, stop and report. Do not start the next one.

---

## 1. Orient before touching anything

Read, in this order:

1. **`CONTEXT.md`** — the glossary. Use its words exactly: frontier, claim,
   worker, dispatcher, verdict, merge hold, escalation, circuit breaker, runner.
   If you need a term that is missing, or the code contradicts a definition, say
   so in your report. Do not silently pick a side.
2. **`docs/adr/`** — all of them. They are binding, not background.
3. **`.scratch/phase-1/spec.md`** — the spec every ticket was cut from. Read the
   sections your ticket touches, at minimum *Implementation Decisions* and
   *Testing Decisions*.
4. **The ticket file.** Its `Blocked by:` line and its acceptance criteria are
   the contract.
5. **`AGENTS.md`**, if it exists yet. Its conventions override anything here.
6. **The module docstring of every file you are about to edit.**

### Pick the ticket (work the frontier)

If the developer named a ticket, use it. Otherwise, scan
`.scratch/*/issues/*.md` and take the **lowest-numbered** ticket that is:

- `Status: ready-for-agent` (bold `**Status:**` counts), and
- has every ticket in its `Blocked by:` line at `Status: done`.

**Never claim a `ready-for-developer` ticket.** Those need the developer's
secrets, GitHub settings or judgement. If the only unblocked tickets are
`ready-for-developer`, say so and stop.

### Housekeeping

- If the folder is **not a git repository or has no remote**, stop and tell the
  developer. Creating the repo and remote is theirs.
- Update the default branch from the remote, then create a branch named
  `ticket/<effort>-<NN>-<slug>` from it. One ticket, one branch. Never commit to
  the default branch.
- Set the ticket's `Status:` to `in-progress` and commit that first.

---

## 2. Tests first — always

Write the tests **from the ticket's acceptance criteria and the spec**, before the
implementation. Then run them and **watch them fail**. A test you have never seen
fail is not known to work.

The spec fixes the test seams. Test through these, from the outside:

| Seam | In | Out |
|---|---|---|
| **Dispatch core** | a world snapshot (tickets, claims, PRs, Jules sessions, escalations, paused flag, config) | a list of actions |
| **Integrity core** | base tree, PR diff, ticket, config, test results | a verdict (`pass` / `fail` / `hold`) with reasons |
| **Bootstrap core** | mode, repo directory, config, secrets | files written and GitHub operations recorded |

Rules that are not optional:

- **Assert behaviour, not implementation.** Assert which tickets get started,
  what the verdict and reasons are, and which files appear. Never assert on
  private attributes or on how something was computed.
- **No live network calls in tests.** GitHub, Jules and `agy` are reached only
  through adapters, and tests use fakes. Adapter contract tests run against
  **recorded** responses stored as fixtures.
- **Scenario names read like the spec.** For example
  `test_held_blocker_keeps_dependents_off_the_frontier`, not `test_frontier_3`.
- **Never mute a failing test.** No `skip`, `skipif` or `xfail`, no deleted or
  weakened assertions, no loosened tolerances, no narrowed inputs. If you cannot
  make a test pass honestly, escalate (section 6).

---

## 3. Implement

Architecture rules from the spec. These are binding:

- **Cores are pure.** The dispatch, integrity and bootstrap cores do **no I/O**:
  no network, no subprocess, no file writes, no clock reads (pass time in). All
  I/O lives in thin adapters. This keeps every decision testable with a snapshot.
- **The dispatcher keeps no state of its own.** Everything it knows comes from
  GitHub and the Jules API on each run. Never add a state file or database.
- **Never print a secret, a prompt, or a denylist entry.** Actions logs on this
  public repo are public. Reasons may name a file and line, never the matched
  text.
- **Status words are exactly** `ready-for-agent`, `ready-for-developer`,
  `in-progress`, `blocked`, `done`. Parsing accepts bold and plain lines. Any
  other word is a *finding* and is never treated as `done`.
- **Every tunable has one home: the config**, never a literal in logic. That
  covers the reserve, cap, concurrency, stale-claim hours and fix-attempt count.
- Python 3.12+. Few dependencies. Use `logging.getLogger(__name__)`, not `print`,
  except in CLI output.
- **Module docstrings explain WHY**, with a `WHY THIS EXISTS` section recording
  the decision or failure that forced the design. Cite the ADR or spec section.

### Checkpoint as you go (quota safety)

Your quota can run out mid-ticket. So the next session can pick up instead of
restarting:

- At each **acceptance-criterion boundary**, once that criterion's tests pass,
  commit and push the work in progress to the ticket branch.
- Keep one progress note under the ticket's `## Comments`, of **five lines or
  fewer**, and overwrite it each time:
  ```
  Progress (YYYY-MM-DD HH:MM): criteria 1–3 done, tests green.
  Next: criterion 4 — stale-claim release; fixture for 13-hour claim not written yet.
  ```
- Do not checkpoint more often than that. Commits are cheap, but notes and
  re-reads cost tokens.

**If you are resuming:** read the progress note and `git log` on the ticket
branch first, and continue from "Next". Do not redo finished criteria.

---

## 4. Run the full local gate

Run every gate that exists in the repo. Ticket 01 creates them, so before that
ticket lands, run whatever is present. Expected once 01 is done:

```
ruff check .
python scripts/check_tests_first.py
pytest -q
```

All must pass with **zero failures** before you push. If the repo's CI or
`AGENTS.md` names more gates, run those too.

---

## 5. Review your own diff, adversarially

Review as if you were hunting for the reason this will be reverted:

- **Tick each acceptance criterion individually.** If one cannot be ticked, the
  ticket is not done.
- **Break the implementation on purpose** for at least one criterion (flip a
  comparison, drop a check) and confirm a test goes red. Then restore it.
- **Would a dishonest target-repo PR slip through?** This is the question that
  matters most for anything touching the integrity gate or dispatch.
- **Does anything print a secret, prompt or denylist entry?** Grep your diff for
  `print`, logging of prompts, and exception messages that echo input.
- **Did any core gain I/O?** If so, move it into an adapter.
- **Stale docstrings.** Update the reasoning wherever behaviour changed.
- **Line endings.** If `git status` shows far more files changed than you
  touched, stop and investigate.

---

## 6. Land it, or escalate

### Landing (all gates green, review done)

1. In the ticket file: set `Status: done`, tick every criterion (`- [x]`), and
   replace the progress note with a short summary under `## Comments`: what was
   built, which tests cover which criterion, anything the developer should know.
2. Commit the code, tests and ticket file together on the ticket branch, then
   push.
3. Open a PR to the default branch titled `<NN>: <ticket title>`. The body holds
   the criteria checklist, the gate results and the adversarial-review notes.
   Use `gh pr create` if it is available. Otherwise print the compare URL and the
   PR body, ready to paste.
4. **Do not merge.** Every engine ticket is `Auto-merge: no`, so the developer
   merges.
5. Stop, and report in five lines or fewer: ticket, branch, PR, gate results, and
   anything surprising.

### Escalating (three honest attempts at a red gate have failed)

1. Commit the finished work to the ticket branch.
2. Set the ticket's `Status: blocked`.
3. Under `## Comments`, write an **escalation brief** the developer can paste
   straight into a stronger model:
   ```
   ## Escalation — <date>
   Ticket: <NN> <title>   Branch: <branch>
   Goal: <one sentence from "What to build">
   Attempt 1: <what you tried> → <result>
   Attempt 2: ...
   Attempt 3: ...
   Failing output (exact, trimmed to the relevant lines):
   <paste>
   Decision needed: <the one question a human or stronger model must answer>
   ```
4. Push, and open the PR as a **draft**.
5. Stop.

---

## Never

- Start a second ticket in the same session.
- Claim `ready-for-developer` tickets, or simulate their bench or settings work.
- Commit to the default branch, or merge your own PR.
- Mute, skip or weaken a test.
- Call live GitHub or Jules APIs from tests, or put real keys in fixtures.
- Write real people's names, Slack IDs or emails anywhere. Use roles.
- Create a `GEMINI.md`. It would silently override `AGENTS.md`.
