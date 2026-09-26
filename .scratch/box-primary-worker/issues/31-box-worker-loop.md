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

2026-09-26: Implemented `src/ticket_engine/box_worker.py` (`BoxLoop`, `main`)
wiring `BoxCore`/`box_status` to GitHub and `LocalWorker`. Tests in
`tests/test_box_worker.py` (19 new tests), all written first and watched red
before implementation, per the ticket's fakes (GitHub client, a recording
`LocalWorker` fake exposing `run_one`/`fix_ci`/`list_box_claims`, clock, sleep).

- AC1: `box-worker` console script added (`pyproject.toml`); `main(--config,
  --once)`; `LocalWorkerConfig` gains `concurrency`, `poll_interval_minutes`,
  `status_interval_minutes`, `weekly_cap_after_hours`,
  `weekly_cap_backoff_hours`, `engine_repo`, `logs_dir`, loaded by
  `load_local_config`. `test_main_once_runs_exactly_one_tick` and the two
  `local_config` field/load tests.
- AC2: `BoxLoop._build_world` pulls each repo, loads tickets, reads
  `TICKET_ENGINE_PAUSED`, and reuses new `LocalWorker.list_box_claims`
  (reads `list_claim_branches` + each branch's `Claimed-by`, no third copy
  of that logic). One test per step type: `test_claim_ticket_calls_run_one_
  and_appends_ledger`, `test_resume_claim_calls_run_one_for_existing_box_
  claim`, `test_fix_ci_called_for_box_pr_with_red_ci`, `test_wait_step_
  sleeps_for_the_difference`.
- AC3: `_write_status` creates/locks/pins only when `find_open_issue` finds
  none, always updates the body via new `GitHubClient.update_issue_body`/
  `lock_issue`/`pin_issue`. `test_write_status_creates_locks_pins_and_
  updates_body` asserts the whole rendered body round-trips through
  `parse_box_status`; `test_second_write_status_does_not_recreate_lock_or_
  pin` runs two ticks and asserts no second create/lock/pin.
- AC4: `_handle_run_result` applies `BoxCore.after_quota_error`/
  `after_success` and persists the pause record; `_raise_alert`/
  `_close_alert` dedupe via `find_open_issue`; an `auth` outcome raises
  `login_expired` and pauses the loop (in memory — `BoxCore` predates login
  state, so this pacing lives in the loop, documented in the module
  docstring). `test_quota_outcome_applies_after_quota_error_...`,
  `test_success_applies_after_success_...`, `test_raise_alert_dedupes_...`,
  `test_auth_outcome_raises_login_expired`, `test_login_expired_pauses_
  ticks_...`, `test_quota_timeline_weekly_cap_alert_opened_once_then_closed`.
- AC5: `_configure_logging` installs a `RotatingFileHandler` under
  `logs_dir`; no `print()` anywhere in the module. `test_configure_logging_
  writes_to_rotating_file_not_stdout`, `test_agy_failure_text_never_
  reaches_a_github_request_body`, `test_gitignore_excludes_box_logs_dir`,
  `test_module_docstring_explains_local_state_vs_dispatcher_invariant`.
- `LocalWorker` extended (not reopened): `run_one(..., box_mode=True)` and
  `_resolve_outcome` return a `quota`/`auth` outcome immediately instead of
  sleeping/looping internally, so the box-worker loop (not a single blocking
  call) owns pacing across ticks while it can still rewrite the status issue
  and react to alerts. `box_mode` defaults to `False`, so `work-windows` and
  every existing `test_local_worker.py` test are unchanged (verified: full
  suite green, no test edited).
- Adversarial check: removed the `find_open_issue` dedup guard in
  `_raise_alert` on purpose — `test_raise_alert_dedupes_and_close_alert_
  closes_open_one` went red (`create_issue` called twice), confirming the
  test actually guards the behaviour. Restored.
- Gates: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`
  all green (536 passed, 0 failed, 0 skipped).
