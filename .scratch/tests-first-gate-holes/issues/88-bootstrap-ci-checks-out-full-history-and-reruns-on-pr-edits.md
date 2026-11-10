# 88: Bootstrap's CI template checks out full history and re-runs on PR edits and labels

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/tests-first-gate-holes/spec.md` (D1, D6)
**Binding:** ADR 0001 (§2.4, §2.5), ADR 0005, ADR 0013

**Read first:** `src/ticket_engine/bootstrap.py` (`_CI_WORKFLOW_TEMPLATE`, and the
"new" mode that writes it to `.github/workflows/ci.yml`); `tests/test_bootstrap_new.py`
(`_new_input`, `_written`, `test_new_mode_produces_ci_workflow`);
`.github/workflows/ci.yml`.

## What to build

A repo created by `bootstrap` new mode gets a CI workflow whose checkout is depth 1, so
`scripts/check_tests_first.py` cannot see `origin/master` or the PR's base commit and
(before ticket 87) passes every change. Its PR runs also never re-run when the PR body
or labels change, so a `[no-test-needed: ...]` added to the body, or the
`tests-exempt` label, only counts if it was there when the PR opened.

1. `_CI_WORKFLOW_TEMPLATE` in `src/ticket_engine/bootstrap.py`:
   - the `on:` block becomes exactly:

     ```
     on:
       push:
       pull_request:
         types: [opened, synchronize, reopened, edited, labeled, unlabeled]
     ```
   - the `- uses: actions/checkout@v4` step gains, indented under it:

     ```
             with:
               fetch-depth: 0
     ```
   Nothing else in the template changes.
2. `.github/workflows/ci.yml` (this repo): under `pull_request:`, after
   `branches: [master, main]`, add
   `    types: [opened, synchronize, reopened, edited, labeled, unlabeled]`. The
   checkout already has `fetch-depth: 0`; leave it.

Adopt mode does not write `ci.yml` and is not changed. Target repos' own workflows are
changed by their own tickets.

## Acceptance criteria

Tests go in `tests/test_bootstrap_new.py` and call the real `new(_new_input())`, reading
the file with `_written`, as `test_new_mode_produces_ci_workflow` does. Nothing is
faked.

- [ ] `test_new_mode_ci_checks_out_full_history`: the written `ci.yml` contains the
  line `fetch-depth: 0`, and it appears after `uses: actions/checkout@v4` and before
  `uses: actions/setup-python@v5`.
- [ ] `test_new_mode_ci_reruns_on_pr_edits_and_labels`: the written `ci.yml` contains
  `types: [opened, synchronize, reopened, edited, labeled, unlabeled]` on the line
  after `  pull_request:`.
- [ ] `test_new_mode_produces_ci_workflow` still passes unchanged.
- [ ] `.github/workflows/ci.yml` has the step-2 `types:` line under `pull_request:`
  and still has `branches: [master, main]` there. (no test: workflow file)

## Gate

Run all three, in CI's order (`.github/workflows/ci.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
pytest -q
```

## Comments
