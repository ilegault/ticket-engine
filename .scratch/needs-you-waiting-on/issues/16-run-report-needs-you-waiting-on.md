# 16: Run report shows what each developer ticket is waiting on

**What to build:** In the dispatch run report's **Needs you (`ready-for-developer`)** table, every row shows which of that ticket's `Blocked by` tickets are unfinished, with their statuses, or `ready now` when none are. The table is rendered by one shared, pure function that the morning report will reuse in ticket 17. Spec: `.scratch/needs-you-waiting-on/spec.md`.

**Blocked by:** None (can start immediately)

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in `tests/test_run_report.py`. Build every ticket through the real parser with the file's existing `ticket(...)` helper and `epif_chain()` fixture, and find rows with its `table_row(...)` helper. Nothing is faked: `build_run_report` is pure. Write these tests first and watch them fail.

- [ ] **One shared renderer.** `src/ticket_engine/run_report.py` gains a pure function `needs_you_table(tickets: Sequence[Ticket]) -> list[str]`. It returns the header `| Ticket | Title | Waiting on | Holding up |`, the separator `|---|---|---|---|`, and one row per `ready-for-developer` ticket, sorted by number. It returns `[]` when there are none. `build_run_report`'s `# --- Needs you ---` block keeps its heading line and its trailing blank line, and builds the table only by calling `needs_you_table`. A test asserts that the report for `epif_chain()` contains the exact header line above.
- [ ] **Waiting on reuses `unfinished_blockers`.** The cell is `", ".join(unfinished_blockers(t, tickets))`, or `ready now` when that list is empty. Write no new blocker check: the repo's `AGENTS.md` §3 invariant 3 forbids a third copy of the frontier rule. Test: with tickets `34 ready-for-agent`, `35 in-progress`, `36 done` and `43 ready-for-developer` blocked by `34, 35, 36` and titled `Deploy`, `table_row(report, 43) == "| 43 | Deploy | 34 (ready-for-agent), 35 (in-progress) | — |"`.
- [ ] **All blockers done → `ready now`.** With `epif_chain()`, `table_row(report, 36) == "| 36 | Commit the blank EPIF template | ready now | 37, 41, 45, 46 |"`.
- [ ] **Missing and legacy blockers are named, never treated as done.** `48 ready-for-developer` blocked by `99`, titled `Make folder` → `table_row(report, 48) == "| 48 | Make folder | 99 (no such ticket) | — |"`. `50 ready-for-developer` blocked by `49`, where `49` has status `human-task`, titled `Bench step` → `table_row(report, 50) == "| 50 | Bench step | 49 (human-task) | — |"`.
- [ ] **Nothing existing is weakened.** `test_developer_ticket_lists_everything_it_transitively_holds_up` and every other assertion in the file pass unchanged. None are deleted or edited. Reverting the Waiting on cell to a constant `ready now` makes at least two of the new tests fail; check this by hand once before landing.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
