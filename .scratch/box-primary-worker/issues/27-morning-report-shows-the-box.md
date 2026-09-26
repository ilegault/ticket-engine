# 27: The morning report shows the box

**What to build:** The daily morning report says in one line whether the box is available, paused, silent or unreadable, and when it last checked in. Its `Windows-waiting` section becomes `Waiting for the box`, because `Runner: windows` tickets now wait for the box rather than for a Windows worker someone starts by hand. Spec: `.scratch/box-primary-worker/spec.md` (User story 56).

**Blocked by:** 24

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in `tests/test_morning_report.py`. `render_morning_report`, `box_status` and the parser are real, and the report is pure. The runner script's GitHub calls are faked the way the existing tests fake them. Write these tests first and watch them fail.

- [x] **Box line.** `render_morning_report` gains a keyword argument `box: BoxStatus | None`, plus the `now` it already has or a new one. Directly under the report's title it renders exactly one line, using the same four `box_state` words and the same classification function as ticket 24. Import it from `dispatch`; do not write a second copy.
  - `**Box:** available — last check-in 2026-09-26 03:12 UTC`
  - `**Box:** paused — last check-in …`
  - `**Box:** silent — last check-in …`
  - `**Box:** status unreadable`

  One test per state, asserting the whole line.
- [x] **Renamed section.** The line `**Windows-waiting:** <n>` becomes `**Waiting for the box:** <n>`, with the same list beneath it. Rewrite any existing test asserting `Windows-waiting` in place under its current name, asserting the new wording. Delete no test.
- [x] **Runner wiring.** `scripts/run_morning_report.py` fetches the `engine:box-status` issue body from the engine repo it already writes to, and passes `parse_box_status(body)`. A failed fetch passes `None`. A test runs the script's `main` with a faked `_gh_request` returning a rendered status body and asserts the Box line appears in the body sent to `_update_issue`.
- [x] **Docstring.** `morning_report.py`'s `WHY THIS EXISTS` mentions ADR 0006.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

2026-09-26: All acceptance criteria implemented and verified. Split decision: not split (one vertical slice sharing one test file).
- Box line: `dispatch.classify_box(box, now, box_silent_hours)` is extracted from `DispatchCore.evaluate`, which now calls it; `render_morning_report(data, *, box=None)` uses it with `data.now` and `RepoConfig().box_silent_hours` to render one `**Box:**` line under the title. Tests: `test_render_morning_report_box_available`, `_paused`, `_silent`, `_unreadable` (whole line), and `test_render_morning_report_box_uses_dispatch_classification` (report and dispatcher agree for every `BoxState`).
- Renamed section: `**Waiting for the box:** <n>`. Rewritten in place, none deleted: `test_render_morning_report_shows_windows_waiting_tickets`, `test_render_morning_report_blocked_windows_ticket_not_shown`, `test_render_morning_report_needs_you_placement_and_count`, `test_render_morning_report_needs_you_zero_shown_not_hidden`.
- Runner wiring: `scripts/run_morning_report.py` `_fetch_box_status` reads the open `engine:box-status` issue in `ENGINE_REPO` and passes `parse_box_status(body)`; a failed read passes `None`. Tests: `test_run_morning_report_main_passes_box_status`, `test_run_morning_report_main_failed_box_fetch_is_unreadable`.
- Docstring: `morning_report.py` gains `THE BOX (ADR 0006, ticket 27)`.
- Checked by hand: swapping `available`/`paused` in `classify_box` turns 7 tests red; passing `box=None` in the runner turns the wiring test red.
- Base branch was red (41 failures): merge 27941bb dropped ticket 24's `RunFacts` box fields, the live box-status read, the `OVERFLOW (ADR 0006)` docstring and 16 of its tests. A separate commit on this branch restores the code and tests verbatim from 3339e0f; the docstring is restored in the ticket 27 commit.
