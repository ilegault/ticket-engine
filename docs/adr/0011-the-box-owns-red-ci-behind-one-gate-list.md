# ADR 0011 — The box owns red CI on its own PRs, and one gate list runs before the push and in CI

**Status:** accepted
**Date:** 2026-10-07
**Applies to:** the box worker, the local worker's fix runs, target repo CI, the add-repo ruleset, and the ticket skill
**Amends:** ADR 0006 (who escalates a box PR), ADR 0010 rule 4 (the baseline shares the gate runner); supersedes ticket 30's "the dispatcher's existing escalation is the authority"

## Context

On 2026-10-07 a box PR (Slackbot #125, ticket 101) went red on lint — two unused
imports — and sat for hours while the box status issue said `working`. Four gaps
combined:

- The dispatcher's red-CI escalation never runs: the live dispatcher builds its
  snapshot without open PRs. Ticket 30 deferred to it, so nobody escalated.
- The box counted fix attempts in memory, counted runs that pushed nothing, and
  stopped silently at three. The glossary counted only runs that pushed. Neither
  ended in an escalation.
- The fix run reused the full implement-from-scratch prompt (check the frontier,
  claim, set `in-progress`) on a ticket already `done` and claimed, and named only
  the failing check, not its output.
- Lint reached CI at all because the engine trusted the worker's own local gate.
  The box ran nothing itself before pushing.

## Decision

1. **The box owns red CI on its own PRs.** It counts fix attempts in a durable
   ledger under `logs_dir`. A fix attempt counts whether or not the run pushes.
   After `max_fix_attempts` (default 3) it escalates itself, through the same
   escalation the local worker already performs for exhausted resumes: brief,
   draft PR, `engine:escalated` label, one escalation issue. Fix runs go through
   the same outcome handling as implement runs (quota pauses, resumes, waiting).
2. **A fix run gets its own fix prompt**: the ticket is the worker's and already
   `done`; do not claim it or change its status; fix only what failed; never mute
   or weaken a test. It carries the failing output — the local gate's output for
   the repo's own commands, and the tail of the GitHub job log for checks that
   cannot be reproduced locally.
3. **Pre-push gate.** After every worker run and before anything is pushed, the
   box runs the repo's `gate_commands` in the repo environment, then the
   integrity gate locally against the default branch with no GitHub writes.
   Every command runs; each is reported. Any red, or an integrity `fail`, means no
   push: the worker is sent back with all the failing output, and that run counts
   as a resume. A local integrity `hold` does not block the push.
4. **One gate list.** `gate_commands` in the target repo's `.ticket-engine.toml`
   is the only list of the repo's own checks. CI runs it through the engine's
   shared `gate.yml@v1` workflow, which reads `python_version`, `install`,
   `[test_env]` and `gate_commands` from that file, runs every command, and fails
   if any failed. The repo's own lint and test workflows are removed. The baseline
   (ADR 0010 rule 4) uses the same runner.
5. **Exactly two required checks**: `gate` and the integrity gate. `add-repo`'s
   ruleset requires both. Other workflows may exist but are never required;
   `add-repo --check` warns about them.
6. **The dispatcher's PR escalation is Jules-only** and stays unwired until a
   later effort handles red CI on Jules PRs. The dispatcher honours
   `jules_enabled = false` (ADR 0010 rule 1), so a repo can be kept away from
   Jules meanwhile. The ticket skill stops promising Jules the failing CI output.

## Consequences

- The box and CI run literally the same list, so a check cannot exist in one and
  not the other. Adding a check to a repo means adding it to `gate_commands`.
- Most lint, type and tests-first failures never reach GitHub; a red PR is now
  mostly something only CI can see.
- A stall ends in an escalation the developer is told about, never in silence.
- Retrofitting a repo means moving its CI onto `gate.yml` and replacing its
  hand-set required checks — a developer step (ticket 68 for Slackbot and TDS-T8;
  RBL through add-repo).
