# ADR 0015 — Jules is removed, and the box is the only dispatcher

**Status:** accepted
**Date:** 2026-11-20
**Applies to:** the dispatcher, the box worker, the morning report, add-repo and bootstrap, the worker skill
**Supersedes:** ADR 0004 (the engine answers a waiting Jules session)
**Amends:** ADR 0006 (Jules as overflow and handoff), ADR 0010 rule 1 (`jules_enabled`)

## Context

ADR 0006 made the box the primary worker and kept Jules as overflow, started by a
stateless GitHub Action dispatcher. In practice Jules was slow, and a Jules PR
once merged a ticket as `done` with nothing implemented. The developer switched
Jules off. With Jules off, the Action dispatcher quietly stopped doing anything:
it runs live only when a Jules key is present, and otherwise falls back to a dry
run. Four jobs that had nothing to do with Jules died with it: deleting done
tickets' claims, closing resolved escalation issues, releasing stale claims, and
the circuit breaker. Claim branches and escalation issues have been piling up
since.

## Decision

1. **Jules is removed from the engine.** No Jules adapter, no Jules sessions, no
   overflow, no handoff, no waiting-session auto-replies, no Jules config keys, no
   Jules quota line in the morning report, no Jules section in the worker skill.
   A `.ticket-engine.toml` that still sets a Jules key is a ticket problem naming
   the key, never a crash.
2. **The Action dispatcher is retired.** `dispatch.yml` and its callers in target
   repos are deleted. `dispatch --dry-run` stays: it is how the planner checks
   tickets before handing off. The box-silent check and the morning report stay.
3. **The box is the only dispatcher**, and it takes over the Action's
   non-Jules jobs:
   - closing an escalation issue once its ticket is resolved (ADR 0016);
   - the circuit breaker: two escalations in one repo within 24 hours pause that
     repo. The pause is kept on the box and announced by a box alert; closing
     that alert resumes the repo, so the developer can resume from GitHub Mobile.
     The box's token is not widened to write repository variables.
4. **`Claimed-by: jules` stays readable, and is never written again.** Done
   tickets already carry it; rejecting it would turn history into ticket problems.

## Consequences

- When the box is down, nothing implements tickets. The developer runs tickets by
  hand with the parallel ticket skill, as before.
- Stale claims are no longer released by anyone automatically (ADR 0016).
- A repo that is not on the box's repo list (the engine itself, RBL today) gets no
  dispatcher at all; its tickets are worked by hand.
