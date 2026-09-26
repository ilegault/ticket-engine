# 27: The morning report shows the box

**What to build:** The daily morning report says in one line whether the box is available, paused, silent or unreadable, and when it last checked in. Its `Windows-waiting` section becomes `Waiting for the box`, because `Runner: windows` tickets now wait for the box rather than for a Windows worker someone starts by hand. Spec: `.scratch/box-primary-worker/spec.md` (User story 56).

**Blocked by:** 24

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in `tests/test_morning_report.py`. `render_morning_report`, `box_status` and the parser are real, and the report is pure. The runner script's GitHub calls are faked the way the existing tests fake them. Write these tests first and watch them fail.

- [ ] **Box line.** `render_morning_report` gains a keyword argument `box: BoxStatus | None`, plus the `now` it already has or a new one. Directly under the report's title it renders exactly one line, using the same four `box_state` words and the same classification function as ticket 24. Import it from `dispatch`; do not write a second copy.
  - `**Box:** available — last check-in 2026-09-26 03:12 UTC`
  - `**Box:** paused — last check-in …`
  - `**Box:** silent — last check-in …`
  - `**Box:** status unreadable`

  One test per state, asserting the whole line.
- [ ] **Renamed section.** The line `**Windows-waiting:** <n>` becomes `**Waiting for the box:** <n>`, with the same list beneath it. Rewrite any existing test asserting `Windows-waiting` in place under its current name, asserting the new wording. Delete no test.
- [ ] **Runner wiring.** `scripts/run_morning_report.py` fetches the `engine:box-status` issue body from the engine repo it already writes to, and passes `parse_box_status(body)`. A failed fetch passes `None`. A test runs the script's `main` with a faked `_gh_request` returning a rendered status body and asserts the Box line appears in the body sent to `_update_issue`.
- [ ] **Docstring.** `morning_report.py`'s `WHY THIS EXISTS` mentions ADR 0006.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
