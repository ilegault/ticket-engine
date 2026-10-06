# 58: The box works the repos on the repo list, clones new ones, and honours `BOX_PAUSED`

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 53, 56, 57

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0009 rule 4; ADR 0010 rules 3, 6 and 7; ADR 0006; ADR 0007 rule 4

## What to build

Each tick, `BoxLoop` (`src/ticket_engine/box_worker.py`) reads the repo list from
the engine repo and works the repos listed with `box = true`, in list order. A
listed repo with no clone yet is cloned into `projects_dir`. A repo that leaves the
list (or gets `box = false`) gets no new claims, but the box still resumes and
fixes CI on its unfinished box claims. The engine repo variable `BOX_PAUSED` stops
all new claims and shows `paused_by_developer` on the status issue.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests build the
loop with `make_loop`, `FakeWorker` and `make_github()` from
`tests/test_box_worker.py`; git is a fake `git_runner` recording its args; the repo
list is a fake `repo_list_fn`. `projects_dir` and `logs_dir` are under `tmp_path`.
`BoxCore` tests use real `BoxWorld`s as `tests/test_box_core.py` does.

- [ ] **The loop takes its repos from the list.** `BoxLoop.__init__` gains
  `repo_list_fn: Callable[[], list[RepoListEntry]] | None = None`. When it is None,
  the loop uses `config.repos` exactly as now (every existing test keeps passing).
  When set, a new method `_current_entries()` is called at the start of
  `_build_world` and returns `LocalRepoEntry(path=str(Path(config.projects_dir) / name), repo=entry.repo)`
  for each `box = true` entry in list order (`name` is the part after `/`).
  `_build_world` and `_entry_for` use the result. If `repo_list_fn` raises, log
  `logger.warning("repo list unreadable: %s", exc)` and reuse the last successful
  result (`[]` before the first). `build_loop` passes
  `repo_list_fn=lambda: fetch_repo_list(github_client, config.engine_repo)`. Tests
  `test_box_works_listed_repos_in_list_order`,
  `test_box_skips_box_false_entries`,
  `test_unreadable_repo_list_reuses_the_last_one`.
- [ ] **A listed repo with no clone is cloned.** When the entry's path is not a
  directory, run `["git", "clone", f"https://github.com/{repo}.git", path]` through
  `self._git_runner` with the configured git timeout, before the pull. On success,
  add the repo to `box_repos.json` in `logs_dir` (a JSON list of repo names, the
  box's local record of every repo it has cloned). On failure, log a WARNING with
  git's output and leave that repo out of this tick's world. Tests
  `test_missing_clone_is_cloned_and_recorded` (assert the exact clone args and the
  file contents) and `test_failed_clone_leaves_the_repo_out_this_tick`.
- [ ] **A delisted repo only finishes its work.** `BoxRepo`
  (`src/ticket_engine/box_core.py`) gains `accepting_new: bool = True`. `BoxCore`
  rule 5 (`ClaimTicket`) skips a repo whose `accepting_new` is False; rules 3 and 4
  are unchanged. `_current_entries` also returns every repo in `box_repos.json`
  that is no longer listed with `box = true` and whose clone exists, and
  `_build_world` builds those with `accepting_new=False`. Tests in
  `tests/test_box_core.py`: `test_repo_not_accepting_new_gets_no_claim` and
  `test_repo_not_accepting_new_still_resumes_its_box_claim`; in
  `tests/test_box_worker.py`: `test_delisted_repo_is_built_not_accepting_new`.
- [ ] **`BOX_PAUSED` stops new claims.** `BoxWorld` gains
  `developer_paused: bool = False` as its last field. `_build_world` sets it from
  `self.github_client.get_repo_variable(self.config.engine_repo, "BOX_PAUSED")`
  with the same truthiness rule as `TICKET_ENGINE_PAUSED` (move that rule into a
  module-level `_is_truthy(value: str | None) -> bool` and use it for both). `BoxCore`
  rule 5 returns nothing while `developer_paused`. In `_current_box_status`, when
  `developer_paused` and the login has not expired, the state is
  `BoxState.paused_by_developer` with `current` kept. Tests
  `test_developer_pause_blocks_claims_but_not_resume` (box_core) and
  `test_box_paused_variable_sets_paused_by_developer_status` (box_worker, asserting
  the rendered body passed to `update_issue_body` contains
  `State: paused_by_developer`).
- [ ] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are. No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Repo environments, baselines, not-ready repos and alerts (tickets 59–62). A
  clone failure is only logged here.
- Deleting clones or environments. Nothing on disk is ever removed.
- Running more than one agy session at once.

## Comments
