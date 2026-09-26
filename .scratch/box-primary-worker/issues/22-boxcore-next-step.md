# 22: BoxCore decides the box's next step

**What to build:** A new pure core, `ticket_engine.box_core`, with `BoxCore.next_step(world: BoxWorld) -> BoxStep`. Each tick of the box loop (ticket 31) calls it to decide the box's next action: write status, wait, fix red CI, resume its own claim, or claim a new frontier ticket. It also carries the quota timeline: retry at the reset time or hourly, treat 5 hours of failures as the weekly cap, then back off to 12 hours and raise an alert once. It is a core in the sense of `AGENTS.md` §3: plain data in, plain data out, no clock reads. Spec: `.scratch/box-primary-worker/spec.md` (§The box loop). ADR 0006.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in a new `tests/test_box_core.py`. Build tickets through the real parser with a `ticket(...)` helper copied from `tests/test_run_report.py`. `DispatchCore`, `ticket_lint` and the core under test are real. There is nothing to fake, because `now` is a field of `BoxWorld`. Write these tests first and watch them fail.

- [ ] **Types.** `BoxWorld` holds:
  - `repos: list[BoxRepo]`, in config order. `BoxRepo` has `repo`, `tickets`, `config: RepoConfig`, `paused: bool`, `claims: dict[int, str]` (ticket number to claimed-by value), and `open_prs: list[BoxPR]`. `BoxPR` has `ticket_number`, `pr_number`, `ci_failed: bool`, and `fix_attempts: int`.
  - `starts_24h: dict[str, int]`, `concurrency: int`.
  - `quota_first_failure: datetime | None`, `quota_retry_at: datetime | None`, `weekly_cap_alert_open: bool`.
  - `last_status_write: datetime | None`, `status_interval_minutes`, `poll_interval_minutes`, `weekly_cap_after_hours`, `weekly_cap_backoff_hours`, `now`.

  The step types are frozen dataclasses: `WriteStatus()`, `Wait(until)`, `FixCI(repo, ticket_number, pr_number)`, `ResumeClaim(repo, ticket_number)`, `ClaimTicket(repo, ticket)`, `RaiseAlert(kind)`, `CloseAlert(kind)`. `kind` is `AlertKind` if ticket 20 has landed; otherwise define a local `str` enum with the same three values.
- [ ] **Precedence.** One test per rule, and one per adjacent pair proving the higher rule wins. The rules, in order:
  1. `WriteStatus` when `last_status_write` is `None` or older than `status_interval_minutes`.
  2. `Wait(quota_retry_at)` while `now < quota_retry_at`.
  3. `FixCI` for a box-claimed ticket whose open PR has `ci_failed` and `fix_attempts < config.max_fix_attempts`.
  4. `ResumeClaim` for a box-claimed ticket that is not `done` and has no open PR.
  5. `ClaimTicket` for the lowest-numbered ticket from `DispatchCore().compute_frontier(...)` in the first repo in order that is unpaused and has `starts_24h[repo] < config.daily_cap`, where the ticket is unclaimed and `lint_ticket(t) == []`. This only applies while fewer than `concurrency` box claims are unfinished.
  6. Otherwise `Wait(now + poll_interval_minutes)`.
- [ ] **Every runner, one frontier definition.** A `Runner: windows` ticket and a `Runner: any` ticket are both claimable. The frontier comes only from `DispatchCore.compute_frontier`: a test with a `ready-for-agent` ticket blocked by an `in-progress` one gets `Wait`, not `ClaimTicket`. A claimed ticket (any claimed-by value) is never claimed again. A lint-held ticket is skipped for the next one.
- [ ] **The quota timeline**, driven by `BoxCore.after_quota_error(world, reset_at) -> tuple[datetime, datetime, list[BoxStep]]`, which returns the new first-failure time, the retry time, and the alert steps.
  - The first failure records `now`. The retry is `reset_at + 60 s` if `reset_at` is given, else `now + 1 h`.
  - A failure more than `weekly_cap_after_hours` after the first failure gives retry `now + weekly_cap_backoff_hours` and `[RaiseAlert(weekly_cap)]` when `weekly_cap_alert_open` is false, `[]` when it is true.
  - `BoxCore.after_success(world)` returns `(None, None, [CloseAlert(weekly_cap)])` when the alert is open, and `(None, None, [])` otherwise.
  - Assert every returned value at 0 h, 1 h, 5 h 01 m and 17 h 01 m.
- [ ] **Purity.** The module imports nothing from `github`, `jules`, `agy`, `local_worker`, `subprocess`, `urllib` or `time`, and never calls `datetime.now`. A test reads the module source and asserts none of those names appear in its import lines or as `datetime.now(`. `AGENTS.md` §3's layer table is **not** edited (held path). The module docstring's `WHY THIS EXISTS` explains that the box may keep local state but its decisions are pure so every rule is snapshot-testable.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
