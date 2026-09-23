# ADR 0003 — A held ticket blocks its dependents; nothing is stacked on it

**Status:** accepted
**Date:** 2026-09-23
**Applies to:** the dispatcher, and the planner when ordering tickets

## Context

When a PR lands in a merge hold, the tickets that depend on it could either wait,
or be built overnight on top of the held branch ("stacked") so the work is ready
when the developer approves.

Stacking keeps the chain busy but builds on code the developer has not accepted.
A hold exists precisely because the developer's input may change that code, and
any change invalidates everything stacked above it. That is wasted quota at best
and a cascade of rebases at worst. It would also let dependents of an
approval-required ticket run ahead of the approval, which is the thing the hold
is there to prevent.

## Decision

1. **Workers always start from the default branch.** A ticket whose `Blocked by`
   names a held (unmerged) ticket is not on the frontier and waits.
2. **Independent tickets keep moving.** A hold stops only its own dependents.
3. **The planner places approval-required tickets as late in the dependency graph
   as the design allows**, ideally as leaves, so few tickets ever sit behind one.

## Consequences

- One held ticket near the root of a graph can idle a repo. The planner rule is
  the mitigation; the morning report makes the idle state visible.
- The dispatcher needs no stacking, rebasing, or "which branch do I start from"
  logic.
