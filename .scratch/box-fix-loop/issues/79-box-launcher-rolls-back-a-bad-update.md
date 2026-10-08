# 79: `box-launcher` starts the box and rolls back an engine update that breaks it

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0012; ADR 0007

## What to build

The box's scheduled task starts `box-worker` directly. Ticket 80 will make the box
update its own engine code; before that is safe, something must catch an update
that keeps the box from starting and put the last good code back (ADR 0012).

After this ticket the scheduled task runs a **box launcher**, `box-launcher`. It
starts the box worker as a child process, restarts it when it exits, and after an
update that leaves the box failing to start, checks out the last good engine
commit, reinstalls, and records the rollback. The launcher imports nothing from the
rest of the engine, so a broken update cannot break the rollback.

New module `src/ticket_engine/launcher.py`, standard library only:

- `LauncherState` (frozen): `good_commit: str`, `current_commit: str`,
  `updated_at: datetime | None`, `failures: tuple[datetime, ...]`,
  `bad_commit: str`. Stored as `launcher_state.json` in the state folder, ISO UTC.
- Pure `after_exit(state, exit_code, now) -> tuple[LauncherState, Action]` where
  `Action` is `Restart(delay_seconds)` or `Rollback(commit)`:
  exit code 75 (the worker exits 75 after updating, ticket 80) → `Restart(0)`;
  any other code → the failure is recorded, and if `current_commit != good_commit`,
  `updated_at` is set, and three failures fall within 15 minutes after
  `updated_at` → `Rollback(good_commit)`; otherwise `Restart(60)`.
- `main(argv=None, run=<subprocess runner>, sleep=time.sleep, now=<utc now>)`:
  `box-launcher --engine-dir <checkout> --state-dir <folder>` loops forever:
  start `[sys.executable, "-m", "ticket_engine.box_worker"]` with cwd the engine
  checkout, wait for it, apply `after_exit`. `Rollback(c)`:
  `git -C <engine-dir> checkout --detach <c>`, then
  `<sys.executable> -m pip install -e <engine-dir>`, set `bad_commit` to the commit
  that failed and `current_commit` to c, clear failures, and write
  `rollback.json` (`{"bad_commit", "good_commit", "at"}`) in the state folder for
  the worker to report (ticket 80).
- The box worker records its own commit as good: after its first completed tick,
  `BoxLoop` writes `good_commit` and `current_commit` =
  `git -C <engine checkout> rev-parse HEAD` into `launcher_state.json` in
  `logs_dir`. The engine checkout is
  `pathlib.Path(ticket_engine.__file__).resolve().parents[2]`.
- `pyproject.toml`: `box-launcher = "ticket_engine.launcher:main"`.
- `docs/box-setup.md`, `## Start at boot`: the action becomes
  `C:\Users\agent\ticket-engine\.venv\Scripts\box-launcher.exe` with arguments
  `--engine-dir C:\Users\agent\ticket-engine --state-dir <logs_dir>`.

## Acceptance criteria

Write the tests first, in a new `tests/test_launcher.py`, and watch each fail
before changing `src/`. `after_exit` tests are pure. `main` tests inject a fake
`run` that records commands and returns scripted exit codes, a fake `sleep`, and a
scripted clock, and stop the loop by having `run` raise a test-only exception after
the scripted calls.

- [x] **Three failed starts after an update roll back.** Test `test_three_failed_starts_within_15_minutes_of_an_update_roll_back`: `updated_at` 10:00, failures at 10:02, 10:05, 10:09 on a commit that is not the good one → `Rollback(good_commit)`.
- [x] **Otherwise it restarts.** Tests `test_failures_outside_the_window_just_restart` (third failure at 10:20 → `Restart(60)`), `test_update_exit_code_restarts_at_once` (exit 75 → `Restart(0)`), and `test_no_rollback_when_running_the_good_commit`.
- [x] **A rollback checks out, reinstalls and records.** Test `test_rollback_checks_out_the_good_commit_reinstalls_and_writes_the_record`: asserts the exact `git checkout --detach` and `pip install -e` argument lists and the contents of `rollback.json` and `launcher_state.json`.
- [x] **The launcher imports nothing it could break.** Test `test_launcher_imports_only_the_standard_library`: parses `launcher.py` with `ast` and asserts no import of `ticket_engine` or of any module outside `sys.stdlib_module_names`.
- [x] **The worker marks its commit good.** Test `test_worker_records_its_commit_as_good_after_a_tick` (`make_loop` with a git runner answering `rev-parse HEAD` with `abc1234`): after one tick `launcher_state.json` has `good_commit` `abc1234`. Test `test_box_setup_doc_starts_the_launcher` (beside `tests/test_box_setup_doc.py`'s tests) asserts the doc names `box-launcher.exe` and both arguments.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments

2026-10-07: Implemented `ticket_engine.launcher` with `LauncherState`, `after_exit`, `main`, and supporting helpers.
Imports exclusively standard library modules. `BoxLoop` records `good_commit` and `current_commit` on its first completed tick.
Updated `pyproject.toml` with console script `box-launcher` and `docs/box-setup.md` to reference `box-launcher.exe` and its arguments.
Covered by tests in `tests/test_launcher.py`, `tests/test_box_worker.py`, and `tests/test_box_setup_doc.py`.
