# 39: A Claude Sonnet adapter, classified the same way agy.py's driver is

**What to build:** New module `src/ticket_engine/sonnet.py`: a `SonnetResult` frozen dataclass and a `SonnetDriver` class whose `.start(prompt, cwd)` drives `claude -p` headlessly, mirroring `AgyDriver.start`'s shape and outcome vocabulary (`success`, `quota`, `auth`, `timeout`, `failed` — `waiting` is never produced, since `--permission-prompts none` guarantees the run always concludes rather than stopping to ask). Classification reads Claude Code's own documented `stream-json` events rather than a guessed text pattern. Spec: `.scratch/sonnet-quota-fallback/spec.md` (§`sonnet.py` adapter).

**Blocked by:** None (can start immediately)

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in a new `tests/test_sonnet_driver.py`, mirroring `tests/test_agy_driver.py`'s pattern exactly (`test_agy_driver_start_exact_argument_list`, `test_agy_outcome_classification_all_eight_cases`'s parametrize-over-fixtures shape) but for `SonnetDriver`. `run_fn` is fake throughout — no test spawns a real `claude` process. New fixtures go in `tests/fixtures/sonnet/`, one JSON-lines file per scenario. Write these tests first and watch them fail.

- [x] **Exact argument list.** `SonnetDriver(run_fn=fake_run, timeout_seconds=...).start(prompt, cwd="/worktree/path")` calls `run_fn` with `args == ["claude", "-p", prompt, "--output-format", "stream-json", "--permission-mode", "bypassPermissions", "--permission-prompts", "none"]` and `cwd == "/worktree/path"` — no `--bare` anywhere in the list. Assert this the same way `test_agy_driver_start_exact_argument_list` asserts `AgyDriver`'s.
- [x] **Success.** `tests/fixtures/sonnet/success.jsonl`'s last line is `{"type": "result", "subtype": "success", "total_cost_usd": 0.0431, "session_id": "sess-1"}`, exit code `0`. `SonnetDriver.start` returns `SonnetResult(outcome="success", success=True, cost_usd=0.0431, session_id="sess-1")`.
- [x] **Quota, from any of the four retry categories.** `tests/fixtures/sonnet/quota.jsonl` contains a line `{"type": "system", "subtype": "api_retry", "error": "rate_limit"}`, exit code `1`, no `result` line. `SonnetDriver.start` returns `outcome="quota"`, `quota_error=True`, `reset_at=None` (this event carries no reset time; `local_worker.py`'s existing `_seconds_until_reset` default buffer already handles a `None` `reset_at` unchanged). A parametrized case (not a new fixture file) calls the module's classification helper directly with `error="overloaded"`, `"billing_error"`, and `"account_on_hold"` and asserts each also maps to `"quota"`.
- [x] **Auth.** `tests/fixtures/sonnet/auth.jsonl` contains an `api_retry` line with `"error": "authentication_failed"`, exit code `1`, no `result` line. `SonnetDriver.start` returns `outcome="auth"`. The same parametrized case as above also asserts `"oauth_org_not_allowed"` and `"cloud_credential_error"` map to `"auth"`.
- [x] **Failed and timeout.** `tests/fixtures/sonnet/failed.jsonl` (exit code `1`, no `api_retry` line, or one with `"error": "server_error"`) classifies as `outcome="failed"`, the same way `agy`'s `error.json` does for `AgyDriver`. A fake `run_fn` that raises `subprocess.TimeoutExpired` is caught by `SonnetDriver.start` and classified as `outcome="timeout"` — no fixture file needed, the same way `agy`'s `canceled.json`/`interrupted.json` stand in for its own timeout-shaped outcomes.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

2026-09-27: Implemented `src/ticket_engine/sonnet.py` (`SonnetResult`, `SonnetDriver`, `classify_retry_error`), mirroring `agy.py`'s adapter shape.
- AC1: `SonnetDriver.start` invokes the exact argument list (`stream-json`, `bypassPermissions`, `--permission-prompts none`, no `--bare`); tested in `test_sonnet_driver_start_exact_argument_list`.
- AC2: Success reads the final `result` message's `total_cost_usd`/`session_id`; tested in `test_sonnet_outcome_success`.
- AC3: `quota.jsonl` (`rate_limit`) classifies as `quota` with `reset_at=None`; the public helper `classify_retry_error` maps `overloaded`, `billing_error`, `account_on_hold` to `quota` too; tested in `test_sonnet_outcome_quota` and the parametrized `test_classify_retry_error_all_categories`.
- AC4: `auth.jsonl` (`authentication_failed`) classifies as `auth`; `classify_retry_error` also maps `oauth_org_not_allowed`, `cloud_credential_error` to `auth`; tested in `test_sonnet_outcome_auth` and the same parametrized test.
- AC5: `failed.jsonl` (`server_error`, not a quota/auth category) classifies as `failed`; a fake `run_fn` raising `subprocess.TimeoutExpired` is caught in `.start` and classifies as `timeout`; tested in `test_sonnet_outcome_failed` and `test_sonnet_outcome_timeout_from_fake_run_fn_raising_timeout_expired`.
- New fixtures in `tests/fixtures/sonnet/` (`success.jsonl`, `quota.jsonl`, `auth.jsonl`, `failed.jsonl`), one JSON-lines file per scenario, mirroring `tests/fixtures/agy/`.
- Gates run under Python 3.12 (this repo's required runtime; the container's default `pytest` binary is 3.11 and cannot even collect the existing suite, since `box_core.py` uses a PEP 695 `type` statement — unrelated to this change): `ruff check .` clean, `check_tests_first.py` OK, full suite 578 passed.
