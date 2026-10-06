# 65: `add-repo` opens the adopt (or upgrade) PR and the repo-list PR

**Status:** ready-for-agent

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

- [ ] **The adopt PR.** For `StepKind.adopt_pr`: run
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
- [ ] **The upgrade PR.** For `StepKind.upgrade_pr`: the same clone and branch, but
  only `.ticket-engine.toml` is rewritten with `config_upgrade`, the commit message
  is `Upgrade .ticket-engine.toml for add-repo (ADR 0009/0010)`, and the PR title is
  `Upgrade ticket-engine config`. `run_adopt` is not called. Test
  `test_upgrade_pr_changes_only_the_engine_config` (the commit's `git add` names
  only `.ticket-engine.toml`; the file afterwards has no `box_enabled` and parses).
- [ ] **The repo-list PR.** For `StepKind.list_pr`: clone `{engine_repo}` the same
  way, `git checkout -b engine/add-{name}`, append
  `\n[[repos]]\nrepo = "{repo}"\n` to `engine-repos.toml` (plus `box = false` when
  `--no-box` is passed: add that flag to `main`, default off), check the result
  with `parse_repo_list`, then commit `Add {repo} to the repo list`, push, and
  `gh pr create --repo {engine_repo} --base master --head engine/add-{name} --title "Add {repo} to the repo list" --body "Opened by add-repo (ADR 0009). Merging this turns the repo on."`.
  Test `test_list_pr_appends_one_parsable_entry`.
- [ ] **A failed git or gh call stops with its step named**, as in ticket 64, and
  the temporary folder is removed in every case (`shutil.rmtree` in a `finally`).
  Test `test_failed_push_stops_and_cleans_up`.
- [ ] **Existing tests unchanged.** Every existing test passes with its assertions
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
