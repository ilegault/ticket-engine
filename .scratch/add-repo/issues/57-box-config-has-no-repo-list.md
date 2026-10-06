# 57: The box's config has no repo list, and a bad config stops the box

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0009 rule 7; ADR 0010 rules 2–3

## What to build

`load_local_config` (`src/ticket_engine/local_config.py`) silently returns
defaults (no repos) when the config is missing or unparseable, so the box once ran
for hours doing nothing. From now on the box gets its repos from the repo list
(ticket 58), so its local config must not list any. This ticket adds a strict
loader used only by `box-worker`, and two folder settings the box needs. The
`work-windows` command keeps using `load_local_config` unchanged.

## Acceptance criteria

Write the tests first in `tests/test_box_worker.py` and watch each fail before
changing `src/`. Config files are real TOML written into `tmp_path`.
`box_worker.build_loop` and `_configure_logging` may be monkeypatched, as
`test_main_once_runs_exactly_one_tick` does.

- [ ] **Two folder settings.** `LocalWorkerConfig` gains `projects_dir: str` (default
  `str(pathlib.Path.home() / "projects")`) and `envs_dir: str` (default
  `str(pathlib.Path.home() / "envs")`), each via a module-level default function
  like `_default_logs_dir`. `load_local_config` reads both; an empty string falls
  back to the default. Test `test_local_config_reads_projects_and_envs_dirs`.
- [ ] **A strict loader for the box.** Add `class BoxConfigError(ValueError)` and
  `load_box_config(path: pathlib.Path | str | None = None) -> LocalWorkerConfig`
  to `local_config.py`. It raises `BoxConfigError` when the file does not exist
  (message contains the path and `not found`), when it is not valid TOML (message
  contains the path and `unreadable`), or when it has a `repos` key (message
  contains `[[repos]]` and `engine-repos.toml`). Otherwise it returns
  `load_local_config(path)`. Tests: one per case, plus
  `test_load_box_config_accepts_a_config_without_repos`.
- [ ] **`box-worker` refuses to start on a bad config.** `box_worker.main` calls
  `load_box_config(args.config)`. On `BoxConfigError` it calls
  `_configure_logging(_default_logs_dir())` (import `_default_logs_dir` from
  `local_config`), logs `logger.error("box-worker refuses to start: %s", exc)`,
  and returns 2 without calling `build_loop`. Tests
  `test_main_refuses_a_missing_config` and `test_main_refuses_a_config_with_repos`
  (monkeypatched `build_loop` that fails the test if called; assert rc 2 and the
  ERROR record text). `test_main_once_runs_exactly_one_tick` passes unchanged.
- [ ] **The runbook's example config matches.** In `docs/box-setup.md`, in the one
  fenced `toml` block, take out the `[[repos]]` block and add
  `projects_dir = "C:/Users/agent/projects"` and `envs_dir = "C:/Users/agent/envs"`.
  Replace the sentence beginning "`repos` lists every target repo clone" with:
  "The box takes its repos from `engine-repos.toml` in the engine repo (ADR 0009);
  this file must not list any. It clones each repo into `projects_dir` and keeps
  one Python environment per repo in `envs_dir`." Rewrite
  `test_config_example_is_real` in `tests/test_box_setup_doc.py` **in place, same
  name**: the block still contains every `LocalWorkerConfig` field except `repos`
  and `github_token`, and loads with `load_box_config` without raising (in place
  of `len(config.repos) >= 1`).
- [ ] **Existing tests unchanged.** Apart from the one in-place rewrite above, every
  existing test passes with its assertions as they are. No test is deleted,
  skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Reading the repo list or cloning (ticket 58).
- `load_local_config`'s lenient behaviour for `work-windows`.
- The rest of `docs/box-setup.md` (ticket 67).

## Comments
