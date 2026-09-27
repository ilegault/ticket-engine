# ADR 0008 — A new ticket file always holds

**Status:** accepted
**Date:** 2026-09-27
**Applies to:** the integrity gate
**Amends:** ADR 0001 (adds check 8)

## Context

Ticket text becomes an unattended worker's literal instructions: a worker reads
its ticket file and acts on whatever it says, with `--dangerously-skip-permissions`
and nobody watching. The gate's other checks prove a PR is *procedurally* honest —
tests pass, nothing was muted, the boxes are ticked — but none of them read what a
ticket asks for. A PR that adds one brand new ticket file, marks it `done`, ticks
its own boxes, and ships a trivial passing test would sail through every check
exactly like an ordinary worker updating its own ticket's status, because check 6
only holds when a PR changes *more than one* ticket file (a planning rewrite) or
*none* (an unrelated infra change) — a single new ticket is neither.

That is the one place outside content becomes a future agent's actual prompt: a
worker, or in this public repo anyone who can get a PR merged, that can slip a new
ticket file past the gate plants instructions a later, equally unattended worker
will read and carry out as if the developer had planned it.

## Decision

**Check 8: a PR that adds a ticket file which did not exist on the base branch
always holds**, whatever every other check says. `IntegrityCore.evaluate` names
each new path under `.scratch/<effort>/issues/*.md` in the hold reason. This is
unconditional: `Auto-merge: yes`, a clean suite, and every acceptance box ticked
make no difference. Only the developer's own merge lets a new ticket into the
tracker.

A worker editing its **own existing** ticket — flipping `Status:` to `done`,
ticking a box, adding a dated line under `## Comments` — is unaffected. Only a
path absent from the base branch counts as new; check 8 never looks at what
changed inside a file that was already there.

## Consequences

- Every ticket a worker's PR proposes now needs the developer's own click, the
  same as a protected-path change (check 4). That is deliberate: unlike a status
  update, a new ticket is a new instruction for the next unattended run to carry
  out.
- A planning PR that adds several tickets at once already held under check 6; it
  now also holds under check 8, with both reasons listed. Harmless and redundant.
- This does not catch a worker rewriting the body of a ticket it is not assigned
  to, or smuggling instructions into a comment on an existing ticket. Both stay
  out of scope: check 8 only ever looks at whether the *path* is new.
