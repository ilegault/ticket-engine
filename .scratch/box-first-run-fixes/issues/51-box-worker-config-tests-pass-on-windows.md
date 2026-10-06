# 51: Two box-worker config tests pass on Windows

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** none. Found while landing tickets 48-50, where both failed locally on a clean `master`.
**Binding:** `docs/agents/issue-tracker.md` (never mute or weaken a test)

## What to build

Two tests in `tests/test_box_worker.py` fail on Windows:
`test_local_config_loads_box_fields_from_toml` and
`test_main_once_runs_exactly_one_tick`. Earlier reports blamed the machine's real
local config. That is wrong. Each test builds its TOML with
`f'logs_dir = "{tmp_path / "logs"}"\n'`. On Windows `tmp_path` holds backslashes
(`C:\Users\...`), and inside a TOML basic string `\U` and `\I` are escape sequences,
so the file is invalid. `load_local_config` logs
`Failed to parse local config ...: Invalid hex value`, falls back to its defaults,
and the assertions then see `ilegault/ticket-engine` instead of `owner/engine`.
The code under test is correct; the tests build bad input. Fix the tests only.

## Acceptance criteria

Run `pytest -q tests/test_box_worker.py` on Windows and see both tests fail for the
reason above before changing anything.

- [ ] **`test_local_config_loads_box_fields_from_toml` writes valid TOML on every
  OS.** Write the `logs_dir` path as a TOML literal string (single quotes) or with
  `tmp_path.as_posix()`. Its assertions are unchanged, including
  `cfg.logs_dir == str(tmp_path / "logs")`.
- [ ] **`test_main_once_runs_exactly_one_tick` writes valid TOML the same way.**
  Its assertions are unchanged.
- [ ] **Neither test loses an assertion or changes what it asserts.** The fix is
  only how the path is written into the TOML text.
- [ ] **No other `tests/` file builds TOML by interpolating a raw path into a
  double-quoted string.** Search `tests/` for it and fix any other hit the same way.
  If there is none, say so in `## Comments`.
- [ ] **The full suite passes locally on Windows.** `pytest -q` reports no failures
  other than any you list in `## Comments` with their cause.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- `src/ticket_engine/local_config.py` and `box_worker.py`. Do not change how a bad
  config is parsed or logged.
- Any test unrelated to TOML path quoting.

## Comments
