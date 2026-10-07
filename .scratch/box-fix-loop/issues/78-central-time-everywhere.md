# 78: Every time the developer reads is in Central time

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 77

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0007 rules 4 and 5

## What to build

Ticket 77 put the status issue in Central time. Every other time the developer
reads still says UTC. After this ticket every human-facing time uses
`format_display` from `src/ticket_engine/display_time.py`; files the engine reads
back (`box_ledger.json`, `box_pause.json`, `box_readiness.json`,
`fix_attempts.json`) stay ISO UTC.

Places that change: `render_box_alert` and `render_repo_not_ready_alert` (`Since:`
line) in `src/ticket_engine/box_status.py`; `render_morning_report` in
`src/ticket_engine/morning_report.py` (the box line and the report timestamp,
today `"%Y-%m-%d %H:%M UTC"`); the times in `src/ticket_engine/run_report.py`
(today `"%H:%M"` in UTC); `_describe_step`'s `wait until` text in
`src/ticket_engine/box_worker.py`; and the box log line timestamps, set by the
formatter in `box_worker._configure_logging`.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests that assert
today's UTC text are rewritten in place, same names, to the Central text; delete
no test. Fix the clock with the existing `now` parameters; nothing else is faked.

- [ ] **Alerts.** Test `test_box_alert_since_is_central`: `render_box_alert(..., since=2026-07-01 20:11 UTC)` body has `Since: 2026-07-01 3:11 PM CDT`; same for `render_repo_not_ready_alert`.
- [ ] **Morning report.** Test `test_morning_report_times_are_central`: the report timestamp and the box check-in line end in `CDT` or `CST` and contain no `UTC`.
- [ ] **Run report.** Test `test_run_report_times_are_central`: every time in a rendered run report is Central with its zone.
- [ ] **Box log lines.** Each line starts `YYYY-MM-DD h:mm:ss AM|PM CDT|CST LEVEL name: message`. Test `test_box_log_lines_are_central`: with the clock fixed by patching the formatter's converter time to 2026-07-01 20:11:05 UTC, `_configure_logging(tmp_path)` then one `logger.warning("x")` writes a line starting `2026-07-01 3:11:05 PM CDT WARNING`.
- [ ] **Machine files stay UTC.** Test `test_machine_files_stay_utc`: after a claim and a quota pause in a `make_loop` run, `box_ledger.json` and `box_pause.json` hold ISO strings ending `+00:00`.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
