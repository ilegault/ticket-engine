# ADR 0014 — A hollow PR fails, and the gate reads the ticket from the base branch

**Status:** accepted
**Date:** 2026-10-09
**Applies to:** the integrity gate (checks 6 and 9, `Auto-merge`) and the planner
**Amends:** ADR 0001 (check 6 and decision 3), ADR 0013 (check 9)

## Context

Slackbot ticket 42 merged as `done` with nothing built (PR #61, 2026-09-28). A Jules
worker pushed an empty commit. The gate held it because no ticket file had changed, and
its reason said a worker's PR must set its ticket to `done`. The verdict comment is
posted under the developer's account, and Jules acts on that account's comments, so it
took the reason as an instruction: it set the ticket to `done`, ticked all six boxes and
wrote a summary of work that was not in the diff. The gate held again ("marks its ticket
done but changes no test file"). A hold is green (ADR 0005), so the PR showed three green
required checks and a label. The developer merged it a day later while clearing held PRs,
and a deploy ticket that depended on it was marked done the same evening.

The gate did what it was designed to do. The design let a human click through it: the
check that a `done` ticket shipped any work was a hold, and touching any test file
cleared it.

Looking into it found a second gap of the same kind. The gate read the ticket from the
PR, which the worker writes; only `Deletes tests:` came from the base branch (ADR 0005).
So a worker could flip `Auto-merge: no` to `yes`, delete or reword a criterion it had not
met, drop a named test from a criterion, or add the word "rewrite" to excuse a named test
from failing on base. Separately, check 9 checked only that named tests exist whenever a
PR added no new test, because there were then no base results to compare against.

## Decision

1. **A hollow PR fails.** A PR that moves any ticket to `done` from a base status other
   than `ready-for-developer`, and changes nothing outside `.scratch/`, gets `fail`. This
   holds whether the PR changes one ticket file or several. A fail is a red required
   check, and the ruleset add-repo creates has no bypass actors, so nobody can merge it,
   the developer included. The reason names the ticket and says its work is not in the
   PR. It never names a change that would satisfy the gate.
2. **`ready-for-developer` is how a ticket declares it has no code.** The developer marks
   bench tickets `done` in ticket-only PRs. When the base copy says `ready-for-developer`,
   that PR is not hollow; it still holds for the developer's own merge, as before. Only
   the base copy declares it, so a worker cannot claim the exemption after the fact.
3. **The gate reads what a ticket asks for from the base copy.** `Auto-merge:`, the
   acceptance criteria, their named tests, their `(by hand)` and `(no test: <reason>)`
   tags, and the rewrite wording all come from the ticket as it is on the default branch.
   The PR's copy supplies only `Status:`, the ticks and `## Comments`. A base criterion
   that is missing, reworded or unticked in the PR's copy counts as unticked.
4. **Check 9 never skips its failed-on-base half.** When a PR adds no new test, none of
   its named tests can have failed on base, so each fails check 9 unless its base
   criterion says it rewrites an existing test.

## Consequences

- Closing a `ready-for-agent` ticket whose work landed in another PR takes two merges:
  flip it to `ready-for-developer` (a planning edit, held), then mark it `done` (held).
  A ticket created already `done` has no base copy, so a ticket-only PR creating it is
  hollow.
- Editing an in-flight ticket's criteria on the default branch fails its open PR until
  the branch takes the new copy, because the PR's ticks no longer match the base
  criteria. Rewrite criteria before a ticket is claimed, or expect a fix attempt.
- A `ready-for-agent` ticket whose every criterion is tagged `(by hand)` or
  `(no test: <reason>)` can still only be held, not proved, once it changes something
  outside `.scratch/`. A planner who means "no code" writes `ready-for-developer`.
- On the box, the pre-push gate runs the integrity gate locally (ADR 0011), so a hollow
  PR is never pushed; the fix attempt receives the reason instead.
- None of this reaches a target repo until the developer moves `v1`.
