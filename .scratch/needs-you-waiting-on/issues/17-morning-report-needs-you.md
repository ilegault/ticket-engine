# 17: Morning report lists each repo's developer tickets and what they wait on

**What to build:** Each target repo's section of the morning report gains a Needs-you block listing that repo's `ready-for-developer` tickets. It uses the same table as the run report (Waiting on and Holding up), so the daily summary tells the developer what's theirs and what they can do today. Spec: `.scratch/needs-you-waiting-on/spec.md`.

**Blocked by:** 16

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in `tests/test_morning_report.py`, using its existing `_data(...)` helper and fixed `_NOW`. Build the tickets for these tests through the real parser (`TicketParser().parse_text(...)`, as the `ticket(...)` helper in `tests/test_run_report.py` does), not the direct `Ticket(...)` construction of this file's `_ticket` helper. Put them in `WorldSnapshot(repo_name="owner/repo", tickets=[...])`. Nothing is faked: `render_morning_report` is pure. Write these tests first and watch them fail.

- [ ] **Placement and count.** In `src/ticket_engine/morning_report.py`, `_render_repo_section` emits the line `**Needs you (ready-for-developer):** N` (N = number of `ready-for-developer` tickets in `snapshot.tickets`) after the Held block and before the `**Windows-waiting:**` line, followed by a blank line. A test asserts the exact lead line and that it appears after `**Held (awaiting approval):**` and before `**Windows-waiting:**` in the rendered text.
- [ ] **Same table, same renderer.** When N > 0, the lines from `run_report.needs_you_table(snapshot.tickets)` (added in ticket 16) follow the lead line. `morning_report` imports it from `run_report`, and `run_report` does not import `morning_report`. Write no table or blocker logic in `morning_report.py`.
- [ ] **Never two answers.** For tickets `34 ready-for-agent`, `36 done` and `43 ready-for-developer` blocked by `34, 36`, the row starting `| 43 |` in the morning report is character-for-character equal to the row starting `| 43 |` in `build_run_report(tickets, RunFacts())`, and equals `| 43 | Ticket 43 | 34 (ready-for-agent) | — |` (use title `Ticket 43`).
- [ ] **Zero is shown, not hidden.** A snapshot with no developer tickets renders `**Needs you (ready-for-developer):** 0`, and the section contains no `| Ticket | Title | Waiting on | Holding up |` line.
- [ ] **Nothing existing is weakened.** Every existing test in `tests/test_morning_report.py`, including `test_render_morning_report_no_person_names`, passes unchanged. None are deleted or edited.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
