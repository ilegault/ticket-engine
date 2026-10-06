# 52: The box claims from the repo's own default branch

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** none. Found on the box's first TDS-T8 run.
**Binding:** ADR 0006

## What to build

The box could not claim any TDS-T8 ticket. TDS-T8's default branch is `main`, and
its `.ticket-engine.toml` says `default_branch = "main"`. `LocalWorker.run_one`
(`src/ticket_engine/local_worker.py`) gets the claim's base commit with
`get_default_branch_sha(entry.repo, _default_branch_for(entry))`. The
module-level `_default_branch_for` always returns `"master"`, so GitHub answers
404 for `git/ref/heads/master`. The log shows:

    ERROR ticket_engine.local_worker: Failed to get default branch SHA for ilegault/TDS-T8: HTTP Error 404: Not Found
    WARNING ticket_engine.box_worker: claim ilegault/TDS-T8 #19 did nothing; waiting 10 minutes before the next tick

Slackbot only works because its default branch happens to be `master`.

The method `LocalWorker._default_branch(entry)` already reads `default_branch`
from the repo's `.ticket-engine.toml` (via `load_repo_config`). It is used for a
PR's base. Make the claim use it too.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests fake
GitHub with `make_fake_github()` and agy with `AgyDriver(run_fn=...)` from
`tests/test_local_worker.py`. The repo config is real: a `.ticket-engine.toml`
written into `tmp_path`, which is the `LocalRepoEntry` path.

- [ ] **The claim's base commit comes from the configured default branch.** In
  `LocalWorker.run_one`, replace `_default_branch_for(entry)` with
  `self._default_branch(entry)`. The module-level `_default_branch_for` then has
  no callers; take it out of `src/`. New test
  `test_claim_base_uses_the_repos_configured_default_branch`: write
  `default_branch = "main"\n` to `tmp_path / ".ticket-engine.toml"`, call
  `run_one(make_repo_entry(path=str(tmp_path), repo="owner/repo"), make_ticket(9))`,
  and assert `get_default_branch_sha` was called with `("owner/repo", "main")`.
- [ ] **No config still means `master`.** New test
  `test_claim_base_defaults_to_master_without_a_repo_config`: the same call with
  an empty `tmp_path` asserts `get_default_branch_sha` was called with
  `("owner/repo", "master")` (the `RepoConfig` default).
- [ ] **No other hard-coded default branch in the box's path.** `grep -n
  '"master"' src/ticket_engine/local_worker.py src/ticket_engine/box_worker.py`
  prints nothing. State the result under `## Comments`.
- [ ] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are. No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- `GitHubClient.get_default_branch_sha`'s own `"master"` default argument.
- The dispatcher and Actions workflows (they already read `RepoConfig`).
- Bootstrap's `ENGINE_CONFIG_TEMPLATE` (`.scratch/bootstrap-adopt-fixes/`).

## Comments
