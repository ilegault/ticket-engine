# 80: The box updates its own engine code between runs, and reports a rollback

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 79, 70, 77

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0012; ADR 0007 rules 4 and 5

## What to build

The box pulls its target repos every tick but never its own engine checkout, so
every engine fix reached the box only when the developer pulled it by hand at the
box. After this ticket the box updates itself between runs (ADR 0012): when the
engine's default branch has moved, it checks out the new commit, reinstalls, and
exits with code 75; the launcher (ticket 79) starts it again on the new code. A
commit the launcher rolled back is never updated to again, and the rollback is
reported with one box alert.

- `BoxWorld` (`src/ticket_engine/box_core.py`) gains `engine_head: str = ""`,
  `running_commit: str = ""`, `bad_commit: str = ""`. A new step
  `UpdateEngine(commit: str)`. In `BoxCore._next_step` it comes right after rule 1
  (`WriteStatus`) and before rule 2: chosen when `engine_head` and
  `running_commit` are both non-empty, differ, and `engine_head != bad_commit`.
  Each tick is between runs, since a run happens inside a single step.
- `BoxLoop._build_world` fills `engine_head` from
  `github_client.get_default_branch_sha(self.config.engine_repo, "master")`,
  `running_commit` from `git -C <engine checkout> rev-parse HEAD`, and `bad_commit`
  from `launcher_state.json` in `logs_dir`.
- `BoxLoop` carries out `UpdateEngine(c)`: `git -C <engine> fetch origin master`,
  `git -C <engine> checkout --detach <c>`, `<sys.executable> -m pip install -e <engine>`,
  writes `current_commit = c` and `updated_at = now` into `launcher_state.json`,
  then calls the injected `exit_fn(75)` (default `sys.exit`). A failing git step
  logs a warning and does not exit.
- **Rollback alert.** `AlertKind` gains `engine_rolled_back`;
  `render_box_alert` gains `commit: str | None = None`, accepted only when it
  matches `[0-9a-f]{7,40}`, adding the body line `Commit: <commit>`. Title
  `Box alert: engine update rolled back`. When `rollback.json` exists in
  `logs_dir`, the loop raises that alert once (through `_raise_alert`, which
  already checks for an open issue first) naming `bad_commit`, then renames the
  file to `rollback.reported.json`.
- **Status shows the engine.** `BoxStatus` gains `engine_commit: str = ""` and
  `engine_updated_at: datetime | None = None`, rendered as
  `Engine: <first 7 of commit>, updated <format_display(time)>` (`Engine: <commit>`
  when never updated) and parsed back.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. `BoxCore` tests
are pure. Box loop tests use `make_loop`, `make_github()` and a fake git runner that
answers `rev-parse HEAD`; `exit_fn` is a recorder.

- [ ] **The engine moved → update.** Tests `test_engine_moved_gives_update_step` and `test_update_comes_after_write_status_and_before_quota_wait`.
- [ ] **Never back onto a bad commit.** Test `test_bad_commit_is_not_updated_to`: `engine_head == bad_commit` → not `UpdateEngine`.
- [ ] **The update checks out, reinstalls and exits 75.** Test `test_update_step_checks_out_reinstalls_and_exits_75`: asserts the three command argument lists in order, `launcher_state.json`'s `current_commit`, and `exit_fn` called with 75. Test `test_failed_checkout_does_not_exit`.
- [ ] **A rollback is reported once.** Test `test_rollback_record_raises_one_alert_naming_the_bad_commit`: `rollback.json` present; after two ticks exactly one issue titled `Box alert: engine update rolled back` was created, its body has `Commit: <bad sha>`, and `rollback.reported.json` exists. Rewrite `test_render_box_alert_titles_and_bodies` in place to include the new kind.
- [ ] **The status shows the engine.** Test `test_box_status_round_trips_the_engine_line`.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
