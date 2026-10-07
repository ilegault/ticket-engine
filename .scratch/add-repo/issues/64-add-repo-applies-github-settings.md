# 64: `add-repo` applies the GitHub settings

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 63

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0009 rule 2; ADR 0002 rules 2 and 3

## What to build

`add-repo owner/name` (without `--check`) carries out the settings steps of its
plan through `gh`: secrets from the secrets file, labels, auto-merge, secret
scanning, push protection, and Actions enabled with all actions allowed. Workflow
permissions are left at their default (read): the engine writes with
`PIPELINE_TOKEN`, never with the workflow token. A failed step stops the command;
re-running it picks up from what is still missing, because the plan is recomputed.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests use the
recording fake `gh` from ticket 63's tests; it also records `stdin`.

- [ ] **Each settings step is one exact `gh` call.**
  `apply_step(step, repo, gh, secrets) -> None` runs, for:
  `SetSecretOp(name)` → `gh secret set {name} --repo {repo}` with the value on
  **stdin** (never in the argument list);
  `CreateLabelOp` → `gh label create {name} --repo {repo} --color {color} --description {description}`;
  `EnableAutoMergeOp` → `gh api -X PATCH repos/{repo} -F allow_auto_merge=true`;
  `EnableSecretScanningOp` → `gh api -X PATCH repos/{repo} --input -` with stdin
  `{"security_and_analysis": {"secret_scanning": {"status": "enabled"}}}`;
  `EnablePushProtectionOp` → the same with `secret_scanning_push_protection`;
  `ActionsPermissionsOp` → `gh api -X PUT repos/{repo}/actions/permissions -F enabled=true -f allowed_actions=all`.
  One exact-args test per op.
- [ ] **Secret values never leave stdin.** Test
  `test_secret_value_only_on_stdin`: with `JULES_API_KEY=jk-value` in the secrets
  file, no recorded argument list and no captured stdout/stderr or log record
  contains `jk-value`; the stdin of the `gh secret set JULES_API_KEY` call equals it.
- [ ] **Missing secret values stop before any write.** If the plan has a
  `SetSecretOp` whose name is not in the secrets file, `main` prints
  `secrets file has no {name}` and returns 2 before running any write. Test
  asserts zero write calls were recorded.
- [ ] **A failed step stops the run.** A `gh` call returning non-zero makes `main`
  print `step failed: {kind}: {last line of gh output}` and return 1, with no later
  step run. Test `test_failed_step_stops_and_names_it`.
- [ ] **Re-running is safe.** Test `test_rerun_after_success_plans_no_settings_steps`:
  facts reflecting the applied settings produce a plan with no settings steps.
  `main` runs the settings steps, then prints `not implemented yet: <remaining step kinds>`
  and returns 1 while later steps remain (until tickets 65–66).

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Workflow permissions and "Allow GitHub Actions to create and approve pull
  requests": left untouched.
- PRs, the ruleset, the dry run (tickets 65–66).

## Comments
