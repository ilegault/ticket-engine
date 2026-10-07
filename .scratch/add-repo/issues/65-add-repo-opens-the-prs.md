# 65: `add-repo` opens the adopt (or upgrade) PR and the repo-list PR

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 64

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0009 rules 2–4; ADR 0002 (PII scan stops a push)

## What to build

`add-repo` opens the two PRs that wire a repo in. In the target repo: the adopt PR
(files from `run_adopt`) for a repo never adopted, or a small upgrade PR (only
`.ticket-engine.toml`, via `config_upgrade`) for one already adopted. In the
engine repo: a PR adding the repo's `[[repos]]` entry to `engine-repos.toml`.
Both are on fixed branch names, so a re-run finds an open PR instead of opening a
second one. The developer merges both by hand (an adopt PR changes `.github/`, so
the integrity gate holds it anyway).

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests use the
recording fake `gh`, and a recording fake `git(args, cwd) -> (code, output)`. The
fake `gh repo clone` copies a fixture repo folder from `tmp_path` into the target
folder; `run_adopt` and `config_upgrade` run for real on that copy.

- [x] **The adopt PR.** For `StepKind.adopt_pr`: run
  `gh repo clone {repo} {tmp}/{name}` (a fresh `tempfile.mkdtemp()`), then
  `git checkout -b engine/add-repo`, then
  `run_adopt(clone, engine_version="v1")`, then apply
  `config_upgrade(text, no_jules)` to the written `.ticket-engine.toml` when it
  returns text. If `AdoptResult.pii_findings` is non-empty, print each finding's
  **file path only** and return 1 before any commit. Otherwise `git add -A`,
  `git commit -m "Wire {repo} to ticket-engine (add-repo)"`,
  `git push -u origin engine/add-repo`, and
  `gh pr create --repo {repo} --base {default_branch} --head engine/add-repo --title "Wire to ticket-engine" --body "Opened by add-repo (ADR 0009). Merge by hand."`.
  Test `test_adopt_pr_commands_in_order` (exact recorded sequence) and
  `test_pii_finding_stops_before_commit_and_prints_paths_only`.
- [x] **The upgrade PR.** For `StepKind.upgrade_pr`: the same clone and branch, but
  only `.ticket-engine.toml` is rewritten with `config_upgrade`, the commit message
  is `Upgrade .ticket-engine.toml for add-repo (ADR 0009/0010)`, and the PR title is
  `Upgrade ticket-engine config`. `run_adopt` is not called. Test
  `test_upgrade_pr_changes_only_the_engine_config` (the commit's `git add` names
  only `.ticket-engine.toml`; the file afterwards has no `box_enabled` and parses).
- [x] **The repo-list PR.** For `StepKind.list_pr`: clone `{engine_repo}` the same
  way, `git checkout -b engine/add-{name}`, append
  `\n[[repos]]\nrepo = "{repo}"\n` to `engine-repos.toml` (plus `box = false` when
  `--no-box` is passed: add that flag to `main`, default off), check the result
  with `parse_repo_list`, then commit `Add {repo} to the repo list`, push, and
  `gh pr create --repo {engine_repo} --base master --head engine/add-{name} --title "Add {repo} to the repo list" --body "Opened by add-repo (ADR 0009). Merging this turns the repo on."`.
  Test `test_list_pr_appends_one_parsable_entry`.
- [x] **A failed git or gh call stops with its step named**, as in ticket 64, and
  the temporary folder is removed in every case (`shutil.rmtree` in a `finally`).
  Test `test_failed_push_stops_and_cleans_up`.
- [x] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are. No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Waiting for the merge, the ruleset, the dry run (ticket 66).
- Editing a target repo's `AGENTS.md` beyond what `run_adopt` already writes.

## Comments

### Summary (2026-10-06)

- Implemented `apply_adopt_pr`, `apply_upgrade_pr`, and `apply_list_pr` in `src/ticket_engine/add_repo.py`:
  - `apply_adopt_pr`: Clones target repo to tempdir, checks out `engine/add-repo`, runs `run_adopt(clone, engine_version="v1")`, applies `config_upgrade(text, no_jules)` to `.ticket-engine.toml`. If PII findings exist, prints each finding's file path only and returns 1 before any commit. Otherwise commits, pushes, and creates PR with title "Wire to ticket-engine".
  - `apply_upgrade_pr`: Clones target repo to tempdir, checks out `engine/add-repo`, applies `config_upgrade(text, no_jules)` to `.ticket-engine.toml`, stages only `.ticket-engine.toml`, commits with "Upgrade .ticket-engine.toml for add-repo (ADR 0009/0010)", pushes, and creates PR with title "Upgrade ticket-engine config". Does not call `run_adopt`.
  - `apply_list_pr`: Clones engine repo to tempdir, checks out `engine/add-{name}`, appends `[[repos]]` entry (including `box = false` when `--no-box` is passed), validates with `parse_repo_list`, commits with "Add {repo} to the repo list", pushes, and creates PR titled "Add {repo} to the repo list".
  - Temporary folders are cleaned up via `shutil.rmtree(tmp_dir, ignore_errors=True)` in a `finally` block in every case.
  - Added `GitRunner`, `default_git_runner`, and `--no-box` flag to `main()`.
- Tests in `tests/test_add_repo.py`:
  - `test_adopt_pr_commands_in_order`: verifies exact git and gh command sequence.
  - `test_pii_finding_stops_before_commit_and_prints_paths_only`: verifies PII stops before commit and prints relative path only without leaking content or pattern.
  - `test_upgrade_pr_changes_only_the_engine_config`: verifies only `.ticket-engine.toml` is staged and upgraded without running `run_adopt`.
  - `test_list_pr_appends_one_parsable_entry`: verifies engine-repos.toml appending and parsing both with and without `--no-box`.
  - `test_failed_push_stops_and_cleans_up`: verifies failed git push raises StepFailedError, names the step, and removes the temp directory.

