# 41: Sonnet fallback is configured, not hardcoded, and wired into box-worker/work-windows

**What to build:** `LocalWorkerConfig` (`local_config.py`) gains `sonnet_enabled: bool = False` and `sonnet_timeout_seconds: int = 7200`, both flat top-level TOML keys (the same shape `concurrency`/`poll_interval_minutes` already use, not nested under `[agy]`). `box_worker.build_loop` and `cli.run_work_windows` — the two real places that construct `AgyDriver` and `LocalWorker` from config today — each also construct a `SonnetDriver(timeout_seconds=config.sonnet_timeout_seconds)` and pass it as `LocalWorker`'s `sonnet_driver`, but only when `sonnet_enabled` is `True`; otherwise `sonnet_driver=None`, so an unconfigured box behaves exactly as it does today. `docs/box-setup.md` (ticket 32) is updated in the same PR, since its own test reads these exact fields. Spec: `.scratch/sonnet-quota-fallback/spec.md` (§Config).

**Blocked by:** 39, 40

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Config-parsing tests extend `tests/test_local_worker.py`'s `test_load_local_config_from_toml`/`test_load_local_config_missing_file_returns_defaults` pair. Wiring tests are new — neither `build_loop` nor `run_work_windows` has a direct construction test today. Nothing here needs a real `claude` or `agy` process. Write these tests first and watch them fail.

- [x] **New keys parse with the stated defaults.** `load_local_config` on a TOML file with neither key set returns `LocalWorkerConfig(sonnet_enabled=False, sonnet_timeout_seconds=7200)`. A TOML file setting `sonnet_enabled = true` and `sonnet_timeout_seconds = 3600` round-trips both values, the same way the existing `test_load_local_config_from_toml` proves out `print_timeout`.
- [x] **`build_loop` disabled by default.** `box_worker.build_loop(config)` with `sonnet_enabled=False` returns a `BoxLoop` whose `.worker.sonnet_driver` is `None`.
- [x] **`build_loop` enabled wiring.** `build_loop(config)` with `sonnet_enabled=True` and `sonnet_timeout_seconds=3600` returns a `BoxLoop` whose `.worker.sonnet_driver` is a `SonnetDriver` with `timeout_seconds == 3600` — not merely present, the configured value actually flows through.
- [x] **`run_work_windows` gets the same treatment.** `cli.run_work_windows` (currently untested directly) constructs `LocalWorker` the same conditional way `build_loop` does: a new test fakes its `GitHubClient`/`AgyDriver`/`SonnetDriver` construction (inspect `run_work_windows`'s body before writing this test, so the fake matches how it actually builds these objects) and asserts `sonnet_enabled=True` in the loaded config results in a `SonnetDriver` being passed to `LocalWorker`, and `sonnet_enabled=False` results in `sonnet_driver=None`.
- [x] **The setup runbook and its own test stay honest.** `docs/box-setup.md`'s `Local config` fenced TOML block gains `sonnet_enabled = false` and `sonnet_timeout_seconds = 7200` as flat top-level keys — otherwise ticket 32's own `tests/test_box_setup_doc.py::test_config_example_is_real` fails the moment `LocalWorkerConfig` gains these two fields, since that test asserts every dataclass field except `repos`/`github_token` appears in the doc's example. The `## agy login` heading is renamed `## agy and Claude logins` and gains a short paragraph describing the one-time, interactive `claude` login under the `agent` account (the same shape as the existing `agy` paragraph — no API key, no `--bare`, drawing on the developer's own Claude subscription). `tests/test_box_setup_doc.py`'s `REQUIRED_HEADINGS` list is updated to match the renamed heading — this is the one existing assertion this ticket deliberately changes, not breaks by accident.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

2026-09-27: Added `sonnet_enabled`/`sonnet_timeout_seconds` to `LocalWorkerConfig` (flat top-level TOML keys) and wired them into `box_worker.build_loop` and `cli.run_work_windows`, each constructing a `SonnetDriver(timeout_seconds=...)` only when `sonnet_enabled` is `True`, else `sonnet_driver=None`.
- AC1: `test_load_local_config_sonnet_keys_default` and `test_load_local_config_sonnet_keys_round_trip`.
- AC2/AC3: `test_build_loop_sonnet_disabled_by_default` and `test_build_loop_sonnet_enabled_wiring` in `tests/test_box_worker.py`.
- AC4: `test_run_work_windows_sonnet_enabled_passes_sonnet_driver` and `test_run_work_windows_sonnet_disabled_passes_none` in `tests/test_cli.py`, faking `GitHubClient`/`AgyDriver`/`SonnetDriver`/`LocalWorker` construction after inspecting `run_work_windows`'s body.
- AC5: `docs/box-setup.md`'s TOML example gains `sonnet_enabled = false` / `sonnet_timeout_seconds = 7200`; `## agy login` renamed `## agy and Claude logins` with a new paragraph on the one-time `claude` login; `tests/test_box_setup_doc.py::REQUIRED_HEADINGS` updated to match (the one deliberate existing-test change).
- Gates run under Python 3.12 (see ticket 39's comment on the container's 3.11 `pytest`): `ruff check .` clean, `check_tests_first.py` OK, full suite 596 passed (6 new tests added).
