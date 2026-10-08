# 77: The box status issue shows the live step, the last PR and starts, in Central time

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 70, 72

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0007 rule 4; ADR 0006; ADR 0010 rule 5

## What to build

The box status issue today says only `Checked in`, `State`, `Current` and
`Paused until`, in UTC, and it is rewritten only between ticks, which can be
hours apart while agy runs. While PR #125 sat red it said `working` the whole time.

After this ticket it reads like this (fixed template, ADR 0007; the dispatcher, the
morning report and the box-silent check still parse it):

```
## Box status
Checked in: 2026-10-07 3:11 PM CDT
State: working
Current: ilegault/slackbot #101
Step: fixing CI (2/3)
Last PR: ilegault/slackbot #102, PR #124, 2026-10-07 1:05 PM CDT
Started (24h): ilegault/slackbot 3/10, ilegault/tds-t8 0/10
Paused until: none
Not ready: none
```

`Step` is one of `implementing`, `pre-push gate red (resume <n>/<m>)`,
`fixing CI (<n>/<m>)`, `waiting for quota`, `none`. The engine commit line is
added by ticket 80.

- New module `src/ticket_engine/display_time.py`: `format_display(dt) -> str`
  giving `YYYY-MM-DD h:mm AM|PM CDT|CST` in `America/Chicago`, and
  `parse_display(text) -> datetime | None` (timezone-aware, UTC). The zone is one
  module constant, `DISPLAY_TIMEZONE = "America/Chicago"`, and the `ZoneInfo` is
  built inside the functions, never at import. Windows has no system time-zone
  database, so `pyproject.toml` gains the dependency `tzdata; sys_platform == "win32"`.
- `BoxStatus` (`src/ticket_engine/box_status.py`) gains `step: str = "none"`,
  `last_pr: LastPR | None = None` (frozen: `ref: TicketRef`, `pr_number: int`,
  `opened_at: datetime`), and `starts: tuple[tuple[str, int, int], ...] = ()`
  (repo, starts in the last 24 h, `daily_cap`). `render_box_status` writes the
  layout above with `format_display`; `parse_box_status` reads it, and still reads
  the old `YYYY-MM-DDTHH:MMZ` body so a dispatcher and a box on different engine
  versions keep working.
- **The step is live.** `LocalWorker.__init__` gains
  `status_hook: Callable[[str], None] | None = None`. It is called with
  `implementing` before every implement or resume run, with
  `pre-push gate red (resume <n>/<m>)` when ticket 72's gate sends agy back, and
  with `fixing CI (<n>/<m>)` at the start of `fix_ci` (n from ticket 70's ledger,
  m = `max_fix_attempts`). `BoxLoop` passes a hook that rewrites the status issue
  at once with that step. `Last PR` is recorded in `box_last_pr.json` in
  `logs_dir` when `_maybe_open_pull_request` opens a PR.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Rewrite
`test_render_box_status_exact_body`, `test_render_box_status_paused_body` and
`test_every_public_box_line_is_on_the_allowlist` in place, same names, to the new
layout; do not delete any test. Box loop tests use `make_loop` and `make_github()`, whose
`update_issue_body` calls record each body.

- [x] **Central time, both seasons.** Tests `test_format_display_uses_cdt_in_summer_and_cst_in_winter` (2026-07-01 20:11 UTC → `2026-07-01 3:11 PM CDT`; 2026-12-01 21:11 UTC → `2026-12-01 3:11 PM CST`) and `test_parse_display_inverts_format_display`.
- [x] **The new body round-trips.** Test `test_box_status_round_trips_step_last_pr_and_starts`: render then parse gives back an equal `BoxStatus`, with dates on both sides of the daylight-saving switch.
- [x] **The old body still parses.** Test `test_parse_box_status_still_reads_the_old_utc_body`: the exact body `render_box_status` produced before this ticket parses to the same check-in time, state and current ticket.
- [x] **The status shows a fix run while it runs.** Test `test_status_issue_shows_fixing_ci_with_attempt_count_during_a_fix_run`: a red box PR with one recorded attempt; during the `fix_ci` call the recorded bodies include `Step: fixing CI (2/3)`.
- [x] **Readers still work.** Test `test_morning_report_reads_the_new_status_body`: `render_morning_report` given a parsed new-layout status shows the box's check-in time and state (copy the existing box tests in `tests/test_morning_report.py`).

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments

2026-10-08: Added `display_time` (Central time, `tzdata` on Windows), `LastPR`/`step`/`starts` on `BoxStatus` with the new render/parse (old UTC body still parses), `LocalWorker.status_hook` (implementing, gate-red resume, fixing CI from the ledger) and `box_last_pr.json`, and `BoxLoop` installing the hook to rewrite the issue at once. Tests: `tests/test_display_time.py` (criteria 1), `tests/test_box_status.py` (2, 3), `tests/test_box_worker.py` (4), `tests/test_morning_report.py` (5), plus `tests/test_local_worker.py` for the hook and last-PR record. Rewrote in place (same names) the three named tests and also `test_render_box_status_lists_not_ready_repos`, whose exact body changed with the layout; no test deleted. The layout renders `Not ready: none` when empty, as in the ticket example. Run the gate with `tzdata` installed on Windows (`pip install -e .`).
