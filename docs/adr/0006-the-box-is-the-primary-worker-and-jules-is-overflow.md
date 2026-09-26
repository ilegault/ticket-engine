# ADR 0006 — The box is the primary worker; Jules is overflow

**Status:** accepted
**Date:** 2026-09-26
**Applies to:** the dispatcher, the local worker, the Jules adapter, and the ticket skill

## Context

In Phase 1, Jules is the only worker for `Runner: any` tickets, and the local
worker only takes `Runner: windows` tickets, run by hand. Jules is too slow for the
volume of tickets the developer wants worked overnight.

Phase 2 adds the box: a Windows mini PC that runs the local worker permanently.
The model still runs in Google's cloud; the box only runs `agy`, git and the
tests. Running `agy` headless from a script is a supported workflow and draws on
the developer's Google AI Pro quota: a five-hour window, plus a weekly cap.

Three designs were considered: retire Jules, keep Jules as a fallback for when the
box is off, or keep Jules as overflow for when the box cannot take work. Moving
the dispatcher onto the box was also considered, so that one program could route
between both workers. That would give the dispatcher state of its own, and it
would stop Jules whenever the box was off.

## Decision

1. **The dispatcher stays a stateless GitHub Action.** It reads everything from
   GitHub and the Jules API on each run, as before.
2. **The box is the highest-priority worker and takes any ticket**, including
   `Runner: windows`. It pulls work itself: it claims the next frontier ticket
   through the normal claim mechanism. It makes only outbound connections.
3. **Jules is overflow.** The dispatcher starts Jules only on `Runner: any`
   tickets, and only when the *box status issue* shows the box is paused on quota
   or silent. Otherwise it leaves the frontier to the box.
4. **Every claim records its worker.** When a ticket is claimed, a
   `Claimed-by: box` or `Claimed-by: jules` line is written into the ticket file on
   the claim branch. It merges with the PR, so a finished ticket says who did it.
5. **A box claim with no progress for 8 hours is stale** and is released. This is
   a config value (default 8). Eight hours is more than one five-hour quota
   window, so a claim still paused after it is almost certainly behind the weekly
   cap, or the box is dead. Either way the ticket must not be held hostage. Jules
   claims keep the 12-hour rule.
6. **Handoff.** When Jules takes a released box ticket, it continues from the
   checkpoint (the pushed branch and progress note) if one exists. If nothing was
   pushed, it starts the ticket fresh. `Runner: windows` tickets are never handed
   to Jules. They wait for the box.
7. **A box that has lost its claim drops the ticket.** When the box resumes and
   finds the claim released or held by Jules, it never pushes to that ticket
   again. It moves to the next frontier ticket, or waits if there is none.

## Consequences

- If the box is off, Jules keeps working `Runner: any` tickets. Only the Windows
  tickets stall.
- Two workers can now write to the same ticket over its lifetime, but never at the
  same time: rules 5–7 hand a ticket over whole.
- The dispatcher's overflow decision depends on the box status issue. A box that
  stops updating it looks silent and triggers overflow. That is the safe failure
  direction.
- Phase 1's local worker (`work-windows`) is extended rather than replaced. Its
  frontier widens from `windows` tickets to all tickets.
