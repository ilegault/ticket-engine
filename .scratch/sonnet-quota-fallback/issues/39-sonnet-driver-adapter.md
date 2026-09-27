# 39: A Claude Sonnet adapter, classified the same way agy.py's driver is

**What to build:** New module `src/ticket_engine/sonnet.py`: a `SonnetResult` frozen dataclass and a `SonnetDriver` class whose `.start(prompt, cwd)` drives `claude -p` headlessly, mirroring `AgyDriver.start`'s shape and outcome vocabulary (`success`, `quota`, `auth`, `timeout`, `failed` — `waiting` is never produced, since `--permission-prompts none` guarantees the run always concludes rather than stopping to ask). Classification reads Claude Code's own documented `stream-json` events rather than a guessed text pattern. Spec: `.scratch/sonnet-quota-fallback/spec.md` (§`sonnet.py` adapter).

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in a new `tests/test_sonnet_driver.py`, mirroring `tests/test_agy_driver.py`'s pattern exactly (`test_agy_driver_start_exact_argument_list`, `test_agy_outcome_classification_all_eight_cases`'s parametrize-over-fixtures shape) but for `SonnetDriver`. `run_fn` is fake throughout — no test spawns a real `claude` process. New fixtures go in `tests/fixtures/sonnet/`, one JSON-lines file per scenario. Write these tests first and watch them fail.

- [ ] **Exact argument list.** `SonnetDriver(run_fn=fake_run, timeout_seconds=...).start(prompt, cwd="/worktree/path")` calls `run_fn` with `args == ["claude", "-p", prompt, "--output-format", "stream-json", "--permission-mode", "bypassPermissions", "--permission-prompts", "none"]` and `cwd == "/worktree/path"` — no `--bare` anywhere in the list. Assert this the same way `test_agy_driver_start_exact_argument_list` asserts `AgyDriver`'s.
- [ ] **Success.** `tests/fixtures/sonnet/success.jsonl`'s last line is `{"type": "result", "subtype": "success", "total_cost_usd": 0.0431, "session_id": "sess-1"}`, exit code `0`. `SonnetDriver.start` returns `SonnetResult(outcome="success", success=True, cost_usd=0.0431, session_id="sess-1")`.
- [ ] **Quota, from any of the four retry categories.** `tests/fixtures/sonnet/quota.jsonl` contains a line `{"type": "system", "subtype": "api_retry", "error": "rate_limit"}`, exit code `1`, no `result` line. `SonnetDriver.start` returns `outcome="quota"`, `quota_error=True`, `reset_at=None` (this event carries no reset time; `local_worker.py`'s existing `_seconds_until_reset` default buffer already handles a `None` `reset_at` unchanged). A parametrized case (not a new fixture file) calls the module's classification helper directly with `error="overloaded"`, `"billing_error"`, and `"account_on_hold"` and asserts each also maps to `"quota"`.
- [ ] **Auth.** `tests/fixtures/sonnet/auth.jsonl` contains an `api_retry` line with `"error": "authentication_failed"`, exit code `1`, no `result` line. `SonnetDriver.start` returns `outcome="auth"`. The same parametrized case as above also asserts `"oauth_org_not_allowed"` and `"cloud_credential_error"` map to `"auth"`.
- [ ] **Failed and timeout.** `tests/fixtures/sonnet/failed.jsonl` (exit code `1`, no `api_retry` line, or one with `"error": "server_error"`) classifies as `outcome="failed"`, the same way `agy`'s `error.json` does for `AgyDriver`. A fake `run_fn` that raises `subprocess.TimeoutExpired` is caught by `SonnetDriver.start` and classified as `outcome="timeout"` — no fixture file needed, the same way `agy`'s `canceled.json`/`interrupted.json` stand in for its own timeout-shaped outcomes.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.
