# 75: Rewrite every open ticket so each criterion names a test

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0013 rule 3; ADR 0008

**An agent must not claim this ticket.**

## What to build

Ticket 76 makes ticket lint refuse any `ready-for-agent` ticket with a criterion
that names no test, and ticket 74's check 9 enforces named tests at merge time
(ADR 0013, no grandfathering). Every open `ready-for-agent` ticket written before
that must be rewritten first, or it stops being picked up.

This is planning work, done in a planning session, not by an agent. For every open
`ready-for-agent` ticket in Slackbot, TDS-T8 and ticket-engine (Slackbot 103–110
first, then add-repo 61, 62, 66 and 67, then TDS-T8's open tickets): give each
acceptance criterion the backticked name of the test that proves it, or tag it
`(by hand)` or `(no test: <reason>)`. Criteria that rewrite an existing test say
"rewrite" so check 9 treats them correctly. The rewritten ticket files go up in one
PR per repo; ADR 0008 holds them for the developer's click.

## Acceptance criteria

- [ ] Slackbot tickets 103–110 each name a test, or carry a tag, on every criterion; the dispatcher dry run on a Slackbot clone shows no "Ticket problems" once ticket 76 is merged. (by hand)
- [ ] ticket-engine add-repo tickets 61, 62, 66 and 67 do the same. (by hand)
- [ ] TDS-T8's open `ready-for-agent` tickets do the same. (by hand)
- [ ] The rewrite PRs are merged by the developer. (by hand)

## Gate

Not applicable (no code changes).

## Comments
