# 31: The box-worker loop runs the box unattended

**What to build:** A new console script, `box-worker`, that runs forever under the box's `agent` account. Each tick it builds a `BoxWorld` from GitHub and its local files, asks `BoxCore.next_step`, carries the step out through `LocalWorker`, and sleeps. It keeps the box status issue current: it creates, pins and locks the issue if missing, and rewrites its body every 30 minutes from `render_box_status`. It raises and closes box alerts. It keeps a local start ledger and quota-pause record. All logs go to a gitignored folder. `box-worker --once` runs a single tick. `work-windows` is unchanged. Spec: `.scratch/box-primary-worker/spec.md` (§The box loop, §Box status issue and alerts). ADR 0006, ADR 0007 rules 4 and 5.

**Blocked by:** 20, 22, 30

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in a new `tests/test_box_worker.py`. Fake the GitHub client, `LocalWorker` (a recording fake exposing `run_one`, `fix_ci` and `list_box_claims`), the clock and `sleep`. `BoxCore`, `box_status`, the parser and the ledger and pause-record files (on `tmp_path`) are real. Write these tests first and watch them fail.

- [x] **Entry point and config.**
  - `pyproject.toml` gains the console script `box-worker = "ticket_engine.box_worker:main"`.
  - `main(argv)` accepts `--config <path>` and `--once`.
  - `LocalWorkerConfig` gains `concurrency=1`, `poll_interval_minutes=10`, `status_interval_minutes=30`, `weekly_cap_after_hours=5`, `weekly_cap_backoff_hours=12`, `engine_repo="ilegault/ticket-engine"` and `logs_dir` (default `<home>/ticket-engine-box/logs`), all loaded from TOML by `load_local_config`.
  - A test runs `main(["--once", "--config", <tmp toml>])` against fakes and asserts it returns `0` after exactly one step.
- [x] **Tick builds the world and carries out the step.** `BoxLoop.tick()` fills `BoxWorld` from:
  - each configured repo's freshly pulled tickets (`git -C <path> pull --ff-only`, then the existing `_load_tickets_from_path`);
  - `get_repo_variable(repo, "TICKET_ENGINE_PAUSED")` for `paused`;
  - `list_claim_branches` plus each claim branch's `Claimed-by`;
  - the box's open PRs and CI;
  - the ledger and pause record.

  It then dispatches on the step type. `ClaimTicket` calls `run_one` and appends `{repo, ticket, started_at}` to the ledger. `ResumeClaim` calls `run_one`. `FixCI` calls `fix_ci`. `Wait` calls `sleep` for the difference. One test per step type asserts the recorded effect.
- [x] **Status issue.** On `WriteStatus`, the loop finds the open issue labelled `engine:box-status` in `engine_repo` (`find_open_issue`, ticket 26). If there is none, it creates it with title `Box status` and that label, then calls new `GitHubClient.lock_issue` (`PUT /repos/{repo}/issues/{n}/lock`) and `pin_issue` (the GraphQL `pinIssue` mutation through the existing `_graphql`). It then updates the body (new `update_issue_body`, `PATCH`) with `render_box_status(...)` built from the current state and `now`.
  - Assert the whole body the fake received.
  - Run two ticks with the issue existing and assert no second create, lock or pin.
- [x] **Quota and alerts.**
  - After `run_one` reports a `quota` outcome, the loop applies `BoxCore.after_quota_error` and writes the pause record (a JSON file in `logs_dir`).
  - After a `success`, it applies `after_success`.
  - `RaiseAlert(kind)` finds or creates the issue from `render_box_alert(kind, owner, since)` with label `engine:box-alert`, where `owner` is `engine_repo.split("/")[0]`. `CloseAlert(kind)` closes the open one.
  - An `auth` outcome raises `login_expired`.
  - A test drives quota failures across a fake clock from 0 h to 5 h 01 m and asserts exactly one `weekly_cap` issue was created. A later success closes it.
- [x] **Logs stay local.**
  - `box-worker` configures a `logging.handlers.RotatingFileHandler` in `logs_dir`. It never logs to stdout the text of any `agy` output.
  - `.gitignore` gains `logs/`.
  - A test asserts the text of a fake `agy` failure appears in the log file on `tmp_path` and in no recorded GitHub request body.
  - The module docstring's `WHY THIS EXISTS` explains why the box may keep a ledger and pause record while the dispatcher keeps none (`AGENTS.md` §3 invariant 2 is about the dispatcher).

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

2026-09-26: Implemented the box-worker loop as `ticket_engine.box_worker.BoxLoop` plus its `main()` console-script entry point.
- AC1: `pyproject.toml` gains `box-worker = "ticket_engine.box_worker:main"`; `main(argv)` parses `--config`/`--once` and (as an injectable seam for tests only, never used by the console script itself) an optional `loop_factory`; `LocalWorkerConfig` gains all six new fields with the spec's defaults, loaded by `load_local_config`; tested in `test_pyproject_registers_box_worker_console_script`, `test_local_worker_config_gains_box_worker_fields`, `test_local_worker_config_box_worker_defaults`, `test_main_once_runs_exactly_one_tick_and_returns_0`.
- AC2: `BoxLoop._build_world` re-pulls each configured repo, reloads its tickets, reads `TICKET_ENGINE_PAUSED`, resolves `Claimed-by` per claim branch, and reads open PRs/CI for box-claimed tickets; `_carry_out` dispatches on the step type; tested in `test_claim_ticket_calls_run_one_and_appends_ledger`, `test_resume_claim_calls_run_one_for_unfinished_box_claim`, `test_fix_ci_calls_local_worker_fix_ci`, `test_wait_step_sleeps_for_the_difference`.
- AC3: `_write_status` finds-or-creates+locks+pins the status issue then always updates its body from `render_box_status`; tested in `test_write_status_creates_locks_pins_and_updates_body` (whole-body assertion) and `test_write_status_twice_creates_locks_pins_only_once`.
- AC4: `on_outcome` (see below) drives `_apply_quota_error`/`_apply_success`, which call `BoxCore.after_quota_error`/`after_success` and persist the pause record; `_raise_alert`/`_close_alert` are idempotent through `find_open_issue`; an `auth` outcome calls `_raise_alert(login_expired)` directly (`BoxCore` itself has no auth rule — see design note below); tested in `test_quota_outcome_applies_after_quota_error_and_writes_pause_record`, `test_success_outcome_applies_after_success_and_clears_pause_record`, `test_auth_outcome_raises_login_expired_alert`, `test_raise_alert_is_idempotent_and_close_alert_closes_it`, and the full-timeline `test_quota_failures_from_0h_to_5h01m_raise_one_weekly_cap_alert_then_success_closes_it`.
- AC5: `_configure_logging` attaches a `RotatingFileHandler` under `logs_dir` to the root logger; `.gitignore` gains `logs/`; the module's `WHY THIS EXISTS` explains the ledger/pause-record local state against AGENTS.md §3 invariant 2; tested in `test_gitignore_lists_logs_dir`, `test_configure_logging_writes_to_a_rotating_file_not_stdout`, `test_fake_agy_failure_text_stays_in_the_log_and_out_of_github_request_bodies`.
- **Design decision (not fully specified by the ticket or spec):** `LocalWorker.run_one`/`fix_ci` return only a `bool`/the final `AgyResult` and already do their own quota-retry sleeping *inside* one call (ticket 29/30), which can block for hours in production — so the box loop cannot learn of a `quota`/`auth`/`success` outcome from the return value alone in time to update its pause record and status/alert issues promptly. Added an optional, purely observational `on_outcome` callback to `LocalWorker.run_one` and `fix_ci` (default `None`, invoked with each raw `AgyResult` the instant it is produced, never consulted for any decision inside `local_worker.py`). This changes no existing behaviour or return value — the 517 pre-existing tests, including the `is True`/`is False` identity assertions in `tests/test_local_worker.py`, pass unmodified — and is covered by two new tests, `test_run_one_on_outcome_observes_quota_then_success_without_changing_result` and `test_fix_ci_on_outcome_observes_the_agy_result`.
- **Design decision:** fix-attempt counts (`BoxPR.fix_attempts`) are kept in an in-memory dict keyed by `(repo, pr_number)`, not on disk. GitHub's check-runs response carries no history of prior box attempts, and the spec's "everything else is re-read from GitHub" principle argues against inventing a second on-disk ledger; a restart under-counting a few attempts only means the dispatcher's own `EscalatePRAction`/`max_fix_attempts` (the acknowledged authority per ticket 30) escalates a little later, never wrongly.
- **Design decision:** `last_status_write` is also kept in memory (reset on restart), not re-derived by re-parsing the box's own status issue body on every tick — simpler, and it avoids one extra GitHub read per tick for a value the loop already knows the instant it writes it.
- Gates: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q` (541 passed, up from 517) all green on Python 3.12. One mutation checked by hand: removed the `find_open_issue` idempotency guard in `_raise_alert`; `test_raise_alert_is_idempotent_and_close_alert_closes_it` went red (2 issues created instead of 1), confirming the test guards it; restored and reverified green.
