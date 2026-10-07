# ADR 0013 — Every acceptance criterion is proved by a named test that failed before the code

**Status:** accepted
**Date:** 2026-10-07
**Applies to:** the integrity gate (new check 9), ticket lint, and every ticket the planner writes
**Amends:** ADR 0001's list of integrity checks

## Context

Check 6 asks only that the ticket is `done` with every box ticked — and the worker
ticks the boxes. Check 7 asks only that new tests fail on the base code — one
trivial test satisfies it. A worker once marked a ticket done having built almost
none of it. Nothing tied a ticked criterion to evidence.

## Decision

1. **Check 9.** The gate reads every backticked `test_...` name in the ticket's
   acceptance criteria. Each must exist under the repo's `test_paths` (matched by
   name, any file) and must be among the tests check 7 saw fail on the base code
   — unless its criterion says it rewrites an existing test, which only has to
   exist and pass. A missing or never-failing named test is a `fail`.
2. **Ticket lint requires a named test per criterion.** A `ready-for-agent`
   ticket whose criterion names no `test_...` is a ticket problem, unless the
   criterion is tagged `(by hand)` or `(no test: <reason>)`.
3. **No grandfathering.** Before the lint rule ships, a planning pass rewrites
   every open `ready-for-agent` ticket in every target repo to satisfy it. A
   target repo gets check 9 when the developer moves the `v1` tag.

## Consequences

- A ticket cannot land hollow: passing means writing every test the planner
  named, each red before the code.
- The planner must write criteria an agent cannot fake; every exception is
  visible as a tag in the ticket.
- Rewritten tickets are held for the developer under ADR 0008.
