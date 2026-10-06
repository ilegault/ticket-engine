# ADR 0010 — Each repo gets its own environment on the box, must pass a baseline, and the box can be paused remotely

**Status:** accepted
**Date:** 2026-10-06
**Applies to:** the box worker, the box status issue, the dispatcher's overflow decision, and target repo config

## Context

The box ran every target repo's tests with whatever `python` was on its PATH.
Slackbot worked only because its packages had been installed by hand into the
engine's own virtual environment. With repos added automatically (ADR 0009),
each needs its own Python and packages, and a repo whose suite is red on the box
before any ticket touches it would burn three fix attempts per ticket and trip
the circuit breaker.

The developer also wants to stop and start the box without logging into it.

## Decision

1. **Each target repo declares how it installs**, in its `.ticket-engine.toml`:
   `python_version` and `install` (one command). The Jules setup script is
   generated from the same two keys. `jules_enabled` (default `true`) lets a
   repo opt out of Jules entirely; the dispatcher never starts Jules there.
2. **The box keeps one virtual environment per repo** at
   `C:\Users\agent\envs\<name>`, outside the clone, so worktrees and `git clean`
   never touch it. agy runs with that environment's `Scripts` folder first on
   its PATH. The box rebuilds it when a hash of the repo's dependency files
   changes.
3. **The box finds new repos itself.** Each tick it reads `engine-repos.toml`
   through the GitHub API and clones any new repo with `box = true` to
   `C:\Users\agent\projects\<name>`. A repo removed from the list (or set to
   `box = false`) gets no new claims; work in flight finishes. Nothing on disk
   is deleted.
4. **A baseline gates every repo.** Before claiming any ticket in a repo, the box
   runs the repo's `gate_commands` on its default branch. The repo is **not
   ready** if the baseline is red, the environment fails to build, or the
   default branch has no `.ticket-engine.toml`. The box skips a not-ready repo,
   raises one fixed-template box alert, and retries when the default branch
   changes.
5. **Not ready is published.** The box status issue lists not-ready repos with a
   reason code. The dispatcher treats a not-ready repo like a paused or silent
   box: Jules may overflow onto its `Runner: any` tickets.
6. **Box pause.** A repo variable `BOX_PAUSED` on the engine repo pauses the
   whole box. The box reads it each tick, finishes the ticket in hand, claims
   nothing new, and its status issue shows `Paused by developer`, so the
   dispatcher allows Jules overflow. Per-repo, `box = false` in
   `engine-repos.toml` is the standing setting.
7. **Repo order is priority.** The box takes frontier tickets from repos in
   `engine-repos.toml` order, each limited by its `daily_cap`.

## Consequences

- A red baseline costs one alert, not a stream of escalations.
- The box status issue's fixed-template rule (ADR 0007 rule 4) is kept: reason
  codes only, never test output.
- Running several agy sessions on the box at once is not part of this decision;
  the box still works one ticket at a time.
