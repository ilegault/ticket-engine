# ADR 0012 — The box updates its own engine code between tickets, and rolls back a bad update

**Status:** accepted
**Date:** 2026-10-07
**Applies to:** the box worker, its scheduled task, the box status issue and box alerts
**Amends:** ADR 0007 (what runs on the box without the developer present)

## Context

The box runs an editable install of its own engine checkout. It pulls target
repos every tick but never its own checkout, so every engine fix reached the box
only when the developer pulled it by hand at the box and restarted the task. Fixes
to the box itself sat unused while the box kept failing in the old way.

## Decision

1. **The box updates itself between tickets.** When the engine's default branch
   moves, the box waits until no worker run is in progress, pulls its checkout
   `--ff-only`, reinstalls, and exits; the scheduled task starts it again on the
   new code.
2. **A launcher owns updates and rollback.** The scheduled task runs
   `box-launcher`, which starts the box worker and never imports the code being
   updated. It records the last engine commit the box ran cleanly on. If the box
   fails to start three times within 15 minutes of an update, the launcher checks
   out that commit, reinstalls, opens a fixed-template box alert
   (`Box alert: engine update rolled back`, naming the bad commit), and does not
   update again until the default branch moves past it.
3. **The box status issue shows the engine commit** it runs and when it updated.

## Consequences

- Merged engine code runs on the box without the developer looking. This rests
  on engine PRs passing the integrity gate and on ADR 0007's fenced account.
- A bad engine merge costs at most a few failed starts and one alert.
- The developer no longer needs to be at the box for engine changes; only the
  launcher itself, and the scheduled task that runs it, are still installed by
  hand.
