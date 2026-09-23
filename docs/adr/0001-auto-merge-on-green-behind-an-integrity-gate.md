# ADR 0001 — Auto-merge on green, behind an integrity gate

**Status:** accepted
**Date:** 2026-09-23
**Applies to:** every target repo wired to the engine

## Context

The point of the engine is that tickets get implemented while the developer is
asleep or busy, in dependency order, without a human clicking anything. Each
ticket depends on the one before it being merged. If every PR waits for a human
merge, the chain moves one ticket per human visit, which defeats the purpose.

The obvious objection is the one every target repo's ADR on tests already makes:
an implementing agent is rewarded for a green build, and the cheapest way to turn
a red build green is to stop the tests reporting the problem. With nobody
reviewing, CI is the only thing between an agent and `master`. A CI that checks
"do the tests pass" but not "are the tests still honest" would merge exactly the
move those ADRs forbid.

## Decision

1. **A PR whose CI is green and whose integrity verdict is `pass` merges
   automatically.** That merge is what wakes the dispatcher for the next ticket.
2. **The integrity gate runs after the target repo's own gates** and returns
   `pass`, `fail` or `hold`, always with reasons. It checks at least:
   1. no test newly skipped or marked expected-to-fail;
   2. no test function deleted, no test file losing assertions;
   3. no ratchet file increased;
   4. no change to `.github/`, gate scripts, `docs/adr/`, `AGENTS.md` or
      `CONTEXT.md` → `hold`;
   5. no tests-first escape hatch used (label or commit tag) → `hold`;
   6. the ticket file is `done` with every acceptance box ticked;
   7. the PR's **new** tests fail when run against the base branch's code. A test
      that already passes before the feature exists is not testing the feature.
   It also runs the people-denylist scan (ADR 0002).
3. **A ticket marked `Auto-merge: no` always ends in `hold`**, whatever the
   checks say. The planner sets this on safety-critical tickets.
4. **Red CI is not escalation.** The worker gets three fix attempts; then it
   escalates.

## Consequences

- The gate, not a reviewer, is what makes unattended merging acceptable. Changes
  to the gate are therefore themselves never auto-merged (check 4).
- Check 7 is the expensive one: it runs part of the suite twice. It is also the
  only mechanical check that a test tests behaviour, which is the property the
  developer cares about most.
- A held PR costs the developer one decision in the morning. That is the intended
  price of a risky ticket.
