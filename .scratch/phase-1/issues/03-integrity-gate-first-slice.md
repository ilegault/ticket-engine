# 03: Integrity gate, first working slice

**What to build:** A target repo's PR gets an integrity verdict — `pass`, `fail` or `hold` with reasons — posted on the PR, from a reusable workflow the repo calls. This slice carries checks 1, 2 and 6 from ADR 0001.

**Blocked by:** 01

**Status:** in-progress

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] A pure verdict core takes (base tree, PR diff, ticket file, config, test results) and returns a verdict with reasons.
- [ ] Check 1: a test newly skipped, conditionally skipped or marked expected-to-fail → `fail`, naming the test.
- [ ] Check 2: a deleted test function, or a test file with fewer assertions than on base → `fail`, naming what was lost.
- [ ] Check 6: the PR's ticket file not `done`, or any acceptance box unticked → `fail`.
- [ ] A reusable GitHub workflow runs the gate on a PR and posts the verdict and reasons as a PR comment (updated, not duplicated, on re-runs) and as a check status.
- [ ] The gate prints no secret and no file contents beyond what reasons require.
- [ ] One fixture PR per check proves it fires; one clean fixture proves `pass`. Each fixture test was observed failing before its check existed.

## Comments
