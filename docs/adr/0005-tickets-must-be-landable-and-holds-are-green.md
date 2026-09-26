# ADR 0005 — Tickets must be landable; a ticket may authorise test deletions; a hold is green

**Status:** accepted
**Date:** 2026-09-25
**Applies to:** the integrity gate, the dispatcher, the parser, and the planner
**Amends:** ADR 0001 (check 2, and how the developer approves a hold)

## Context

Slackbot ticket 49 replaced an old BOM layout with a new one. It told its worker to
delete the two test functions that asserted the old layout. Integrity check 2 fails
any deleted test function, with no exception, so no worker could ever land the
ticket. The worker spent a session finding that out, and the PR would have escalated.

Looking into it turned up a second gap. When the gate returned `hold`, it set its
commit status to `pending`. That status is a required check in the target repo's
ruleset, so a held PR could not be merged by anyone, the developer included. ADR 0001
says a held PR "waits for the developer", but the developer had no way to act on it.
Every `Auto-merge: no` ticket, every PR touching a protected path, and every planning
PR was stuck the same way.

## Decision

1. **A ticket may authorise test deletions.** It does this with a
   `Deletes tests:` line listing `<test file>.py::<test name>` entries. Check 2 does
   not fail those deletions. The assertions inside them do not count as lost from
   their file. Every other deletion and every other lost assertion still fails.
2. **Only the base branch's copy of the ticket authorises.** The gate reads the line
   from the ticket as it is on the default branch, which the developer merged. A
   worker adding the line in its own PR changes nothing. An authorised deletion
   therefore passes and auto-merges: the approval was given when the developer merged
   the ticket.
3. **Prefer rewriting a superseded test in place** under the same name, asserting the
   new behaviour. That needs no line and keeps the test's history. `Deletes tests:` is
   for tests that have nothing left to assert.
4. **A hold is green.** The gate reports `success` with a description starting
   `HOLD, merge by hand:`. It labels the PR `engine:hold` and cancels any queued
   auto-merge. The developer approves a hold by merging it themselves. This is safe:
   only a `pass` enables auto-merge, and no worker can merge. A later pass or fail
   removes the label.
5. **Ticket lint.** `ticket_lint.lint_ticket` reports tickets an agent cannot land as
   written. It checks three things only:
   - the ticket tells the worker to delete a named `test_...` function that its
     `Deletes tests:` line does not list;
   - a `Deletes tests:` entry is malformed;
   - the ticket has no acceptance checkboxes.

   The dispatcher does not start a `ready-for-agent` ticket that has findings. The
   dry run and the run report list the findings. The rules are kept narrow on
   purpose: run against all 50 Slackbot tickets, they flag ticket 49 and nothing else.
6. **The planner runs the dry run before handing off** and clears every ticket
   problem it reports (see `AGENTS.md` §10 and the planner template).

## Consequences

- A held PR shows green with an `engine:hold` label. The morning report already lists
  PRs with that label, so held PRs now appear there. Before this, nothing applied the
  label.
- The lint only catches the specific mistakes above. The planner rules in `AGENTS.md`
  §10 carry the rest: protected paths, `Auto-merge: no`, and a gate list that matches
  CI.
- A lint-held ticket stays on the frontier, so the one definition of "blocked"
  (invariant 3) is unchanged. It is simply not started, and the run report says why.
