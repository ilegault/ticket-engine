# 19: agy adapter runs headless for real and classifies every outcome

**What to build:** Every `agy` run the local worker starts is `agy -p <prompt> --output-format json --dangerously-skip-permissions --print-timeout <value>`, and its result is classified into exactly one outcome: `success`, `quota`, `auth`, `timeout`, `waiting` or `failed`. The undocumented pieces Phase 1 assumed (a local quota-status endpoint, a `quota_error` status, `agy --continue`) are removed. A quota error now resumes with a fresh `agy -p` run on the same worktree, carrying the last progress note, instead of `--continue`. Spec: `.scratch/box-primary-worker/spec.md` (§`agy` adapter). ADR 0007 rule 1.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Deletes tests:** tests/test_local_worker.py::test_refuses_to_start_when_quota_below_20_pct, tests/test_local_worker.py::test_proceeds_when_quota_endpoint_unavailable, tests/test_local_worker.py::test_proceeds_when_quota_above_reserve, tests/test_local_worker.py::test_fake_agy_quota_refusal_scenario, tests/test_local_worker.py::test_agy_driver_read_quota_parses_remaining_pct, tests/test_local_worker.py::test_agy_driver_read_quota_returns_none_when_unavailable, tests/test_local_worker.py::test_agy_driver_continue_session_passes_continue_flag, tests/test_local_worker.py::test_quota_error_keeps_claim_and_resumes_with_continue, tests/test_local_worker.py::test_fake_agy_quota_stop_then_resume_scenario

These nine tests assert behaviour this ticket takes out on purpose (the quota endpoint, the 20% pre-flight reserve, `--continue`). Every other test in `tests/test_local_worker.py` stays; where one asserts on config keys that change (`test_load_local_config_from_toml`, `test_load_local_config_missing_file_returns_defaults`, `test_agy_driver_start_parses_quota_error`), rewrite it in place under the same name to assert the new keys and behaviour.

## Acceptance criteria

Tests go in `tests/test_local_worker.py` and a new `tests/test_agy_driver.py`. Fake the subprocess through `AgyDriver(run_fn=...)`, exactly as the existing `test_agy_driver_start_parses_success` does. No test spawns a real `agy`. The `LocalWorker` and `AgyDriver` under change are real. Write these tests first and watch them fail.

- [ ] **Exact argument list.** `AgyDriver.start(prompt, cwd)` calls `run_fn` with `["agy", "-p", prompt, "--output-format", "json", "--dangerously-skip-permissions", "--print-timeout", <print_timeout>]`, where `<print_timeout>` is `LocalWorkerConfig.print_timeout` (new field, `str`, default `"7200"`, TOML key `[agy] print_timeout`). The prompt is one list element, never split. A test asserts the whole list with `==`.
- [ ] **Outcome classification from recorded outputs.** A new `tests/fixtures/agy/` holds one JSON file per case: `success.json` (`"status": "SUCCESS"`, exit 0), `waiting.json` (`WAITING`), `interrupted.json` (`INTERRUPTED`), `canceled.json` (`CANCELED`), `quota.json` (`ERROR`, a message containing `quota`), `auth.json` (`ERROR`, a message containing `login`), `error.json` (`ERROR`, any other message), plus a non-JSON string. `AgyResult` gains `outcome: str`. The mapping: `SUCCESS` + exit 0 gives `success`; `WAITING` gives `waiting`; `CANCELED`/`INTERRUPTED` give `timeout`; `ERROR` whose text matches any of `LocalWorkerConfig.quota_error_patterns` (default `["quota", "rate limit", "exhausted"]`, case-insensitive substring) gives `quota`; `ERROR` matching `auth_error_patterns` (default `["auth", "login", "credential"]`) gives `auth`; anything else, and unparseable output, gives `failed`. The patterns are passed into `AgyDriver`, never literals in the parser. One parametrised test asserts all eight.
- [ ] **Removed, not stubbed.** `AgyDriver.read_quota`, `AgyDriver.continue_session`, `QuotaInfo`, `LocalWorkerConfig.agy_quota_url` and `agy_quota_reserve_pct` no longer exist, and `LocalWorker.run_one` makes no pre-flight quota check. A test asserts `not hasattr(AgyDriver, "continue_session")` and `not hasattr(AgyDriver, "read_quota")`.
- [ ] **Quota resumes with a fresh run.** When the first run's outcome is `quota`, `run_one` keeps the claim (no `delete_branch` call on the fake GitHub client), sleeps via `sleep_fn`, then calls `AgyDriver.start` a second time with the prompt from `_assemble_checkpoint_prompt` (contains `## RESUMING FROM CHECKPOINT` and the progress note the fake `read_ticket_fn` returns). Rewrite `test_resume_fallback_to_fresh_session_when_continue_fails` and `test_fake_agy_resume_fallback_to_fresh_session_scenario` in place so they assert this, with no `--continue` anywhere in the recorded argument lists.
- [ ] **Docstrings.** The `WHY THIS EXISTS` sections of `agy.py` and `local_worker.py` say why the flags are passed (headless soft-denies unapproved tools and exits 0; the default timeout is 5 minutes) and why there is no quota pre-flight (no documented quota source). Breaking one mapping (make `CANCELED` give `failed`) turns the parametrised test red; check this by hand once before landing.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
