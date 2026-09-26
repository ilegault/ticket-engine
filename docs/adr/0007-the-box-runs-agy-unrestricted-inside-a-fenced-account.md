# ADR 0007 — The box runs agy without permission prompts, inside a fenced account

**Status:** accepted
**Date:** 2026-09-26
**Applies to:** the box's setup, the local worker, and everything the box posts to GitHub

## Context

In headless mode, `agy` soft-denies any tool call that needs approval: the run
carries on and still exits 0. Allow-lists for shell commands are unreliable in
headless mode. So a worker that must run git, pytest and the gates unattended
either runs with `--dangerously-skip-permissions`, or keeps hitting silent denials.

The box also holds the developer's personal file storage, and it is logged in to
the developer's own Google account, because the `agy` quota belongs to that
account. The engine and every target repo are public (ADR 0002).

## Decision

1. **The local worker runs `agy` with `--dangerously-skip-permissions`.** CI and
   the integrity gate are the guardrail, not the agent's permission prompts.
2. **It runs under a separate, standard (non-admin) Windows account** (`agent`).
   The developer's storage belongs to his own account, and `agent` is denied
   access to it. A runaway agent can damage only its own account.
3. **The box's GitHub token is fine-grained and narrow.** It covers the target
   repos with Contents, Pull requests and Issues read/write, and Variables read,
   and the engine repo with Issues read/write only. It has no Workflows permission
   and no Administration permission, and it expires. The token lives only in the
   `agent` account's secrets file.
4. **The box posts only fixed-template text to the engine repo.** The box status
   issue and box alerts are short status lines: states, ticket IDs, times. Raw
   error output and logs are never posted. A test enforces this.
5. **Full logs stay on the box**, in a gitignored folder of its local engine
   checkout. They are never committed.
6. **The box needs no inbound ports.** Remote access to the box is a separate
   project and must not open ports while the agent lives there.

## Consequences

- If the box is compromised, an attacker can push branches, open PRs and write
  issues. The integrity gate still stands between those PRs and a merge. They can
  spend the developer's `agy` quota. The developer accepts that risk.
- Reading the box's full logs needs remote access to the box, which is not built
  yet.
- Setting up the box includes a one-time interactive `agy` login under the `agent`
  account.

## Amendment — 2026-09-26

Ticket 31's box-worker loop reads `TICKET_ENGINE_PAUSED` (the dispatcher's
circuit breaker) on every tick, so the box stops claiming tickets in a repo the
dispatcher has paused. That repository variable is not covered by Contents,
Pull requests or Issues, so rule 3 gains `Variables read` on the target repos —
the one non-public read the box needs. Without it, the circuit breaker cannot
stop the box, only the dispatcher. The developer approved this during spec
review.
