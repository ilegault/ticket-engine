# 31: The box-worker loop runs the box unattended

**What to build:** A new console script, `box-worker`, that runs forever under the box's `agent` account. Each tick it builds a `BoxWorld` from GitHub and its local files, asks `BoxCore.next_step`, carries the step out through `LocalWorker`, and sleeps. It keeps the box status issue current: it creates, pins and locks the issue if missing, and rewrites its body every 30 minutes from `render_box_status`. It raises and closes box alerts. It keeps a local start ledger and quota-pause record. All logs go to a gitignored folder. `box-worker --once` runs a single tick. `work-windows` is unchanged. Spec: `.scratch/box-primary-worker/spec.md` (§The box loop, §Box status issue and alerts). ADR 0006, ADR 0007 rules 4 and 5.

**Blocked by:** 20, 22, 30

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in a new `tests/test_box_worker.py`. Fake the GitHub client, `LocalWorker` (a recording fake exposing `run_one`, `fix_ci` and `list_box_claims`), the clock and `sleep`. `BoxCore`, `box_status`, the parser and the ledger and pause-record files (on `tmp_path`) are real. Write these tests first and watch them fail.

- [ ] **Entry point and config.**
  - `pyproject.toml` gains the console script `box-worker = "ticket_engine.box_worker:main"`.
  - `main(argv)` accepts `--config <path>` and `--once`.
  - `LocalWorkerConfig` gains `concurrency=1`, `poll_interval_minutes=10`, `status_interval_minutes=30`, `weekly_cap_after_hours=5`, `weekly_cap_backoff_hours=12`, `engine_repo="ilegault/ticket-engine"` and `logs_dir` (default `<home>/ticket-engine-box/logs`), all loaded from TOML by `load_local_config`.
  - A test runs `main(["--once", "--config", <tmp toml>])` against fakes and asserts it returns `0` after exactly one step.
- [ ] **Tick builds the world and carries out the step.** `BoxLoop.tick()` fills `BoxWorld` from:
  - each configured repo's freshly pulled tickets (`git -C <path> pull --ff-only`, then the existing `_load_tickets_from_path`);
  - `get_repo_variable(repo, "TICKET_ENGINE_PAUSED")` for `paused`;
  - `list_claim_branches` plus each claim branch's `Claimed-by`;
  - the box's open PRs and CI;
  - the ledger and pause record.

  It then dispatches on the step type. `ClaimTicket` calls `run_one` and appends `{repo, ticket, started_at}` to the ledger. `ResumeClaim` calls `run_one`. `FixCI` calls `fix_ci`. `Wait` calls `sleep` for the difference. One test per step type asserts the recorded effect.
- [ ] **Status issue.** On `WriteStatus`, the loop finds the open issue labelled `engine:box-status` in `engine_repo` (`find_open_issue`, ticket 26). If there is none, it creates it with title `Box status` and that label, then calls new `GitHubClient.lock_issue` (`PUT /repos/{repo}/issues/{n}/lock`) and `pin_issue` (the GraphQL `pinIssue` mutation through the existing `_graphql`). It then updates the body (new `update_issue_body`, `PATCH`) with `render_box_status(...)` built from the current state and `now`.
  - Assert the whole body the fake received.
  - Run two ticks with the issue existing and assert no second create, lock or pin.
- [ ] **Quota and alerts.**
  - After `run_one` reports a `quota` outcome, the loop applies `BoxCore.after_quota_error` and writes the pause record (a JSON file in `logs_dir`).
  - After a `success`, it applies `after_success`.
  - `RaiseAlert(kind)` finds or creates the issue from `render_box_alert(kind, owner, since)` with label `engine:box-alert`, where `owner` is `engine_repo.split("/")[0]`. `CloseAlert(kind)` closes the open one.
  - An `auth` outcome raises `login_expired`.
  - A test drives quota failures across a fake clock from 0 h to 5 h 01 m and asserts exactly one `weekly_cap` issue was created. A later success closes it.
- [ ] **Logs stay local.**
  - `box-worker` configures a `logging.handlers.RotatingFileHandler` in `logs_dir`. It never logs to stdout the text of any `agy` output.
  - `.gitignore` gains `logs/`.
  - A test asserts the text of a fake `agy` failure appears in the log file on `tmp_path` and in no recorded GitHub request body.
  - The module docstring's `WHY THIS EXISTS` explains why the box may keep a ledger and pause record while the dispatcher keeps none (`AGENTS.md` §3 invariant 2 is about the dispatcher).

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
