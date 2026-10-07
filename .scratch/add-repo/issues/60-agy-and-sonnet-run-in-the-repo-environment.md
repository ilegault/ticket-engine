# 60: agy and Sonnet run inside the repo's environment

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 52, 59

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0010 rule 2; ADR 0007 rules 1 and 5

## What to build

When the box starts agy (or the Sonnet fallback) on a ticket, the agent's `python`,
`pip`, `pytest` and `ruff` must be the repo environment's, not whatever is first
on the box's PATH. This ticket lets both drivers take extra environment variables
and lets `LocalWorker` supply them per repo. Existing test fakes, which take
`(args, cwd)`, keep working because the new argument is passed only when set.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests fake the
drivers with `run_fn` and git with `_make_git_runner` from
`tests/test_local_worker.py`; the env-passing tests use three-argument fakes.

- [x] **The agy driver passes env through.** In `src/ticket_engine/agy.py`,
  `AgyDriver.start(self, prompt, cwd=None, env: dict[str, str] | None = None)`
  calls `self._run(args, cwd)` when `env` is None (unchanged) and
  `self._run(args, cwd, env)` otherwise. `_default_run` gains a keyword
  `env: dict[str, str] | None = None` and passes `env={**os.environ, **env}` to
  `Popen` when it is set. Tests `test_agy_start_passes_env_to_run_fn` and
  `test_agy_start_without_env_calls_two_argument_run_fn`.
- [x] **The Sonnet driver does the same.** Same change to `SonnetDriver.start` and
  `_default_run` in `src/ticket_engine/sonnet.py`. Tests in
  `tests/test_sonnet_driver.py` mirroring the two above.
- [x] **`LocalWorker` asks for the env per repo.** `LocalWorker.__init__`
  (`src/ticket_engine/local_worker.py`) gains
  `env_for: Callable[[LocalRepoEntry], dict[str, str] | None] | None = None`
  (default: a function returning None). `_start_with_fallback(prompt, cwd, env=None)`
  passes `env` to both drivers. Every caller of `_start_with_fallback` (in
  `run_one` and `fix_ci`) passes `self._env_for(entry)`. Test
  `test_run_one_runs_agy_with_the_repos_env` (an `env_for` returning
  `{"VIRTUAL_ENV": "/envs/repo"}`; the three-argument agy fake records it) and
  `test_fix_ci_runs_agy_with_the_repos_env`.
- [x] **The box supplies the repo environment.** `build_loop`
  (`src/ticket_engine/box_worker.py`) constructs `LocalWorker(..., env_for=...)`
  where the function returns
  `env_vars(env_paths(config.envs_dir, entry.repo, os.name == "nt"), os.environ.get("PATH", ""))`
  from `repo_env`. Test `test_build_loop_wires_env_for_to_the_repo_environment`
  asserts `loop.worker._env_for(make_entry(repo="o/r"))["VIRTUAL_ENV"]` ends with
  `r` under the configured `envs_dir`.
- [x] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are. No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Building the environment (ticket 59 builds it; ticket 61 calls it before work).
- `work-windows` on the developer's PC: it passes no `env_for`, so nothing changes
  there.

## Comments

Implemented on 2026-10-06:
- Added `env` support to `AgyDriver.start` and `_default_run` in `src/ticket_engine/agy.py`, passing `env={**os.environ, **env}` to `Popen` when provided, while preserving 2-argument `self._run(args, cwd)` behavior when `env` is None.
- Added `env` support to `SonnetDriver.start` and `_default_run` in `src/ticket_engine/sonnet.py`, passing `env={**os.environ, **env}` to `subprocess.run` when provided, while preserving 2-argument `self._run(args, cwd)` behavior when `env` is None.
- Added `env_for: Callable[[LocalRepoEntry], dict[str, str] | None] | None = None` to `LocalWorker.__init__` in `src/ticket_engine/local_worker.py`. `_start_with_fallback` forwards `env` to both drivers (only passing `env` kwarg when not None so existing 2-arg fakes remain compatible), and callers (`run_one`, `fix_ci`, `_resume_from_checkpoint`, `_send_auto_reply`) pass `self._env_for(entry)`.
- Wired `env_for` in `box_worker.build_loop` using `repo_env.env_paths` and `repo_env.env_vars`.
- Added tests in `tests/test_agy_driver.py`, `tests/test_sonnet_driver.py`, `tests/test_local_worker.py`, and `tests/test_box_worker.py`.
- Full CI gate passed (ruff check ., check_tests_first.py, pytest -q: 718 passed, 1 skipped).
