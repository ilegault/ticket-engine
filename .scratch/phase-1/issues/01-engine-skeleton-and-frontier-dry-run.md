# 01: Engine skeleton, and a frontier dry-run against Slackbot

**What to build:** The engine repo exists with its own CI, and the developer can point it at a local clone of a target repo and see, without anything happening, exactly which tickets the dispatcher would start and why. This is the first cut of the ticket parser and the pure dispatch core (world snapshot in, actions out; see spec: Implementation Decisions → Dispatch core).

**Blocked by:** None (can start immediately)

**Status:** in-progress

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] The engine repo has a Python package, a `pyproject`, and CI running `ruff check`, the tests-first gate and `pytest` on every push and PR.
- [ ] The ticket parser reads `Status`, `Blocked by`, `Runner` and `Auto-merge` lines whether bold (`**Status:**`) or plain; `Blocked by: None …` means no blockers; missing `Runner` means `any`, missing `Auto-merge` means `yes`.
- [ ] An unknown or legacy status word (`complete`, `completed`, `human-task`, anything else) is a parse finding that is reported, and the ticket is never treated as `done`.
- [ ] The dispatch core is a pure function: given a world snapshot (parsed tickets, existing claims, open PRs, concurrency limit) it returns actions; it performs no I/O.
- [ ] The frontier is `ready-for-agent` tickets whose every blocker is `done`, first by number; `ready-for-developer` tickets never appear in it.
- [ ] `dispatch --dry-run <path-to-clone>` prints the frontier, the start actions it would take (respecting 2 in flight per repo), the skipped `windows` tickets, and the parse findings.
- [ ] Run against a copy of Slackbot, the dry-run's output matches the tickets' actual state (verified by hand and recorded under `## Comments`).
- [ ] Scenario tests drive the core from the outside: fresh set, partial completion, a blocker not done, a `windows` ticket, a legacy status, concurrency limit reached. Each test was observed failing before the code existed.

## Comments
