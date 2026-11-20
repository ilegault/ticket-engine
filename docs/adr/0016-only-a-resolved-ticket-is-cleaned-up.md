# ADR 0016 — Only a resolved ticket is cleaned up, and nothing is released on its own

**Status:** accepted
**Date:** 2026-11-20
**Applies to:** claims, ticket branches, escalation issues, worktrees and local branches; the box, the cleanup workflow and `engine-prune`
**Amends:** ADR 0006 (stale claims and handoff)

## Context

Claim branches, ticket branches, worktrees and local branches outlive their
tickets: nothing deletes them since ADR 0015 retired the Action dispatcher, and
the parallel ticket skill never did. The developer wants them cleaned up, but an
escalated ticket's branch, draft PR and escalation issue carry the record of what
the agents tried and said, and losing that mid-investigation is worse than any
amount of clutter. Once a PR has merged or closed, GitHub keeps its commits and
comments even after the branch is deleted, and the ticket's `## Comments` is on
the default branch.

## Decision

1. **A ticket is resolved when it is `done` on the default branch and has no open
   PR.** Only a resolved ticket's claim, ticket branch, escalation issue, worktree
   and local branch are ever cleaned up. A `blocked` ticket is never resolved, however old.
2. **Cleanup has two owners.** A cleanup workflow in each repo runs when a PR
   merges and, if the ticket is now resolved, deletes its claim and closes its
   escalation issue. The box runs `engine-prune` on its own clones once a day as
   the backstop. GitHub's "automatically delete head branches" setting removes
   ticket branches on merge.
3. **`engine-prune` is a dry run unless told to apply.** It removes only what a
   resolved ticket left behind, and never a worktree with uncommitted or unpushed
   work. Everything it will not touch is listed with the reason, including orphan
   claims (a claim whose ticket is no longer on the default branch).
4. **Nothing is released on its own.** An unresolved ticket's claim is removed
   only by the developer's explicit release (`engine-prune --release
   <effort>/<NN>`): a dry run first, then the claim is deleted and its draft PR
   and escalation issue are closed with a comment saying why. The escalation
   skill may run it, but only after the developer agrees to the dry run's output.
5. **Stale claims are shown, not released.** A claim with no new commit on its
   ticket branch for 8 hours that the box is not resuming is listed on the box
   status issue as needing the developer. The box keeps resuming its own claims.

## Consequences

- A dead hand-worked session's ticket stays claimed until the developer releases
  it; the box status issue says so.
- Re-planning an escalated ticket is a deliberate act with a visible trail on the
  closed PR and issue.
- The box never writes to another session's branch.
