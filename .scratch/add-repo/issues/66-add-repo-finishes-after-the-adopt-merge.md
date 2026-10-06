# 66: `add-repo` waits for the adopt merge, then creates the ruleset, runs the dry run and prints the Jules script

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 65

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0009 rule 2; ADR 0005 (dispatcher dry run clean before handoff)

## What to build

The ruleset requires the integrity check, which exists only once the adopt PR is
merged, so `add-repo` waits for that merge before creating it. While it waits it
prints what the developer must merge. If the developer stops it, re-running
resumes (the plan is recomputed). After the merge it creates the ruleset, runs the
dispatcher dry run against a fresh clone, prints the Jules setup script unless
Jules is off, and ends with the list of manual steps left.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests use the
recording fake `gh`, a fake `sleep`, and monkeypatch
`ticket_engine.add_repo.run_dispatch_dry_run`.

- [ ] **It waits for the adopt merge.** For `StepKind.wait_adopt`: print
  `Merge the adopt PR: https://github.com/{repo}/pull/{n}` (and the list PR's URL
  if one is open), then poll
  `gh pr view {n} --repo {repo} --json state --jq .state` every 60 s through the
  injected `sleep`. `MERGED` continues; `CLOSED` prints
  `adopt PR closed without merging` and returns 1; `KeyboardInterrupt` prints
  `Stopped. Re-run add-repo {repo} to continue.` and returns 130. Tests for each.
- [ ] **The ruleset.** For `CreateRulesetOp`: `gh api -X POST repos/{repo}/rulesets --input -`
  with stdin JSON `{"name": "engine-branch-protection", "target": "branch", "enforcement": "active", "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}}, "rules": [{"type": "required_status_checks", "parameters": {"strict_required_status_checks_policy": false, "required_status_checks": [{"context": "ticket-engine/integrity-gate"}]}}]}`
  (name and context from `ENGINE_RULESET_NAME` and `_INTEGRITY_CHECK_CONTEXT`). Test
  asserts the exact parsed JSON.
- [ ] **The dry run.** For `StepKind.dry_run`: clone the repo into a temporary
  folder as in ticket 65 and call `run_dispatch_dry_run(str(clone))` from
  `ticket_engine.cli`; a non-zero result prints
  `dispatcher dry run reported problems (see above)` and returns 1. The folder is
  removed afterwards. Test with the monkeypatched dry run returning 0 and 1.
- [ ] **The Jules script and the closing summary.** For `StepKind.jules_script`:
  print the script from `_build_jules_setup_script(python_version, install, [])`
  using the repo's merged `.ticket-engine.toml` values, under the heading
  `Paste into Jules (repo settings → environment setup):`. The command ends with
  `Still for you:` followed by one line each, only when relevant: merge the list PR
  (URL), add the repo to the box's token (https://github.com/settings/personal-access-tokens),
  paste the Jules script. With every step done it returns 0, and `main` no longer
  prints `not implemented yet`. Test
  `test_full_run_on_a_fresh_repo_executes_every_step_in_plan_order` (scripted facts
  that change as the fake applies each step).
- [ ] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are. No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Merging anything: the developer merges both PRs.
- Setting the Jules script through an API.

## Comments
