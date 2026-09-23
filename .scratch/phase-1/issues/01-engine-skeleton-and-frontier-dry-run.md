# 01: Engine skeleton, and a frontier dry-run against Slackbot

**What to build:** The engine repo exists with its own CI, and the developer can point it at a local clone of a target repo and see, without anything happening, exactly which tickets the dispatcher would start and why. This is the first cut of the ticket parser and the pure dispatch core (world snapshot in, actions out; see spec: Implementation Decisions → Dispatch core).

**Blocked by:** None (can start immediately)

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [x] The engine repo has a Python package, a `pyproject`, and CI running `ruff check`, the tests-first gate and `pytest` on every push and PR.
- [x] The ticket parser reads `Status`, `Blocked by`, `Runner` and `Auto-merge` lines whether bold (`**Status:**`) or plain; `Blocked by: None …` means no blockers; missing `Runner` means `any`, missing `Auto-merge` means `yes`.
- [x] An unknown or legacy status word (`complete`, `completed`, `human-task`, anything else) is a parse finding that is reported, and the ticket is never treated as `done`.
- [x] The dispatch core is a pure function: given a world snapshot (parsed tickets, existing claims, open PRs, concurrency limit) it returns actions; it performs no I/O.
- [x] The frontier is `ready-for-agent` tickets whose every blocker is `done`, first by number; `ready-for-developer` tickets never appear in it.
- [x] `dispatch --dry-run <path-to-clone>` prints the frontier, the start actions it would take (respecting 2 in flight per repo), the skipped `windows` tickets, and the parse findings.
- [x] Run against a copy of Slackbot, the dry-run's output matches the tickets' actual state (verified by hand and recorded under `## Comments`).
- [x] Scenario tests drive the core from the outside: fresh set, partial completion, a blocker not done, a `windows` ticket, a legacy status, concurrency limit reached. Each test was observed failing before the code existed.

## Comments

### Verification and Dry-run Summary against Slackbot
Executed `dispatch --dry-run C:\Users\IGLeg\PycharmProjects\Slackbot`:
- **Frontier (4 tickets):**
  - 30: The buyer's DM carries the BOM (ready-for-agent, blocker 29 is done)
  - 31: Cancel moves the BOM out of the live folder (ready-for-agent, blocker 29 is done)
  - 32: Edit a posted card (ready-for-agent, blocker 28 is done)
  - 33: A corrected EPIF supersedes the old card (ready-for-agent, blocker 27 is done)
- **Start Actions (concurrency limit 2):**
  - Start 30: The buyer's DM carries the BOM (Runner: any)
  - Start 31: Cancel moves the BOM out of the live folder (Runner: any)
- **Skipped Windows Tickets:** 0 (none in Slackbot)
- **Parse Findings:**
  - `34-create-the-boms-folder-on-the-server.md`: Unknown or legacy status 'human-task'
  - `10-seed-the-buyers-roster.md`: Unknown or legacy status 'human-task done'
  - `25-fix-production-env-paths.md`: Unknown or legacy status 'human-task'

All criteria verified. Test suite covers parser behavior, pure dispatch core scenarios, and dry-run CLI with 22 unit tests passing.
