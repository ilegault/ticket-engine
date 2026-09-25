# Spec: developer tickets show what they are waiting on

**Status:** ready-for-agent

## Problem Statement

The dispatch run report has a **Needs you (`ready-for-developer`)** table. Its only
dependency column is **Holding up**, which lists the tickets that wait on each
developer ticket. It does not say what each developer ticket is itself waiting on.

In the Slackbot repo, two developer tickets were listed: one to place a template on
the production server and deploy, and one to create a folder on the production
server. Both showed `—` under Holding up. The deploy ticket was actually blocked by
twelve unfinished agent tickets, but nothing on the page said so. It looked like
something the developer could do right away, and it wasn't. The developer only
learns this by opening the ticket file and checking each blocker's status by hand.

The morning report, the daily summary issue the developer reads first, does not
list developer tickets at all. So the one daily view of every target repo cannot
say "these are yours, and these are the ones you can do today."

## Solution

Every place the engine lists `ready-for-developer` tickets for the developer shows
two things per ticket:

- **Waiting on**: each `Blocked by` ticket that is not `done`, with its current
  status (or "no such ticket"). If every blocker is `done`, the cell says
  `ready now`.
- **Holding up**: unchanged. The unfinished tickets that cannot start until this
  one is done.

The run report's Needs-you table gains the Waiting on column. The morning report
gains a Needs-you section per target repo, rendered as the same table. There is
one renderer for that table, and both reports call it, so the two views cannot
disagree with each other or with the dispatcher's frontier rule.

## User Stories

1. As the developer, I want each developer ticket in the run report to show which of its blockers are unfinished, so that I don't start a bench or server task too early.
2. As the developer, I want each unfinished blocker shown with its status, so that I can tell "an agent is on it" (`in-progress`) from "nobody has started it" (`ready-for-agent`) from "it's escalated" (`blocked`).
3. As the developer, I want a developer ticket whose blockers are all `done` to say `ready now`, so that I can scan the table for what I can do today.
4. As the developer, I want a blocker number that matches no ticket to show as "no such ticket", so that a typo in a `Blocked by:` line is visible instead of silently blocking forever.
5. As the developer, I want `done` blockers left out of Waiting on, so that the cell lists only what is actually in the way.
6. As the developer, I want a blocker whose status line can't be read (a legacy or unknown word) to count as unfinished and show its raw status, so that the report agrees with the dispatcher, which never treats it as `done`.
7. As the developer, I want the Holding up column kept exactly as it is, so that I can still see how much work each of my tickets unblocks.
8. As the developer, I want the Waiting on column placed before Holding up, so that each row reads left to right: the ticket, what it needs, what it frees.
9. As the developer, I want the morning report to list each target repo's developer tickets with the same Waiting on and Holding up information, so that my daily summary tells me what's mine and what I can act on.
10. As the developer, I want a target repo with no developer tickets to say so with a count of zero in the morning report, so that "none" is distinguishable from "section missing".
11. As the developer, I want the morning report's Needs-you table to match the run report's table cell for cell, so that I never see two different answers about the same ticket.
12. As the developer, I want developer tickets listed in ticket-number order in both reports, so that I can find a ticket quickly.
13. As the developer, I want ticket numbers zero-padded to two digits in both reports, so that they match the rest of the report and the ticket file names.
14. As the planner, I want the report to show a developer ticket's unfinished blockers, so that a `ready-for-developer` ticket placed too early in the dependency graph (ADR 0003) is visible the first time the dispatcher runs.
15. As the planner, I want the "waiting on" logic to be the one the dispatcher's frontier rule already mirrors, so that I'm not reasoning about two definitions of "blocked".
16. As a worker implementing this, I want one shared renderer for the Needs-you table, so that I change the table's shape in one place.
17. As a worker, I want the renderers to stay pure, so that every row can be tested from plain parsed tickets with no GitHub or Jules fake.
18. As a reader of a public Actions summary or engine issue, I want the new column to show only ticket numbers and status words, so that nothing personal or secret is exposed (ADR 0002).

## Implementation Decisions

- **One renderer for the Needs-you table**, in the run-report module, next to the
  existing dependency helpers. It takes the full list of a repo's tickets and
  returns the table's markdown lines: header, separator, and one row per
  `ready-for-developer` ticket, sorted by number. It returns an empty list when
  there are no developer tickets. It is pure.
- **Table shape**, verbatim:
  - header `| Ticket | Title | Waiting on | Holding up |`
  - separator `|---|---|---|---|`
  - row `| NN | <title> | <waiting on> | <holding up> |`
- **Waiting on** comes from the existing `unfinished_blockers` helper: entries
  `NN (<status>)` or `NN (no such ticket)`, joined with `", "`, in `Blocked by`
  order. Empty list → `ready now`. No new blocker logic is written. That helper
  already mirrors the dispatcher's frontier rule (a blocker counts only when
  `is_done()`), and the repo's `AGENTS.md` forbids a third copy.
- **Holding up** keeps its current meaning and format: the transitive dependents
  from `held_up_by`, joined with `", "`, or `—` when there are none.
- **Run report**: `build_run_report` renders its Needs-you section through the
  shared renderer. The section heading `### 🧑‍🔧 Needs you (\`ready-for-developer\`)`
  and its position in the report are unchanged. The section is still omitted when
  there are no developer tickets.
- **Morning report**: each per-repo section gains a Needs-you block after the
  Held block and before Windows-waiting. It reads the snapshot's tickets, which the
  morning-report script already fetches from each target repo's default branch.
  Its lead line follows the section's existing style, verbatim:
  `**Needs you (ready-for-developer):** N`, then the shared table's lines when N > 0,
  then a blank line. When N is 0, the lead line appears with 0 and no table.
- **Dependency direction**: the morning-report module imports the table renderer
  from the run-report module. The run-report module does not import the
  morning-report module.
- **No change** to the parser, the dispatch core, the frontier rule, the live
  dispatcher, the morning-report script's fetching, or any adapter.
- **Privacy (ADR 0002)**: the new cells carry only ticket numbers and status words.

## Testing Decisions

- **A good test here renders a report from real parsed tickets and asserts on the
  exact text the developer reads**: whole table rows, the header line, the lead
  line. Never on intermediate lists or on whether a helper was called.
- **Seam 1: the run report.** Drive `build_run_report` with tickets built through
  the real parser, using the `ticket(...)` helper and `epif_chain()` fixture in the
  existing run-report tests. Cover: a developer ticket with a mix of `done`,
  `ready-for-agent` and `in-progress` blockers (only the unfinished ones named, in
  order); one with all blockers done (`ready now`); one with a missing blocker
  (`no such ticket`); one with a legacy-status blocker (named, not treated as done);
  the exact header line. The existing Holding up assertion keeps passing unchanged,
  because Holding up stays the last column.
- **Seam 2: the morning report.** Drive `render_morning_report` with a
  `MorningReportData` whose snapshot holds developer tickets with finished and
  unfinished blockers. Assert the lead line with its count, and assert that the
  morning report's row for a ticket is **identical** to the run report's row for
  the same tickets (the "never two answers" story). Assert a repo with no developer
  tickets shows the lead line with `0` and no table header.
- **Nothing is faked**: both renderers are pure. No GitHub client, Jules client or
  clock is involved beyond the fixed `now` the morning-report tests already pass.
- **Every new test is seen to fail** before the change, and at least one goes red
  when `unfinished_blockers`' result is ignored (a row that always says `ready now`).
- **No existing assertion is deleted or weakened** in either test file.
- **Prior art**: the run-report tests (`table_row` helper, whole-row equality) and
  the morning-report tests (`_data`, `_ticket`, fixed `_NOW`, per-section assertions).

## Out of Scope

- The "Agent tickets waiting on blockers" table in the run report. It already has a
  Waiting on column.
- Splitting Needs you into separate "do now" and "waiting" tables, or re-sorting
  rows by readiness.
- The morning-report script's ticket fetching, including its swallowed HTTP errors.
  That's a separate defect worth its own ticket.
- Integrity verdict comments and escalation briefs.
- Moving the `v1` tag so target repos pick this up. That's a developer step after merge.
- Any change to what counts as blocked, or to the frontier.

## Further Notes

- Target repos run the engine at tag `v1`. Slackbot's run report and the morning
  report (which runs from this repo's own workflow at `master`) differ here: the
  morning report shows the change as soon as it merges, while Slackbot's dispatch
  summary only shows it after the developer moves `v1`.
- Phase 1 used ticket numbers 01–15. Tickets for this spec continue from 16.
