# 03: Integrity gate, first working slice

**What to build:** A target repo's PR gets an integrity verdict — `pass`, `fail` or `hold` with reasons — posted on the PR, from a reusable workflow the repo calls. This slice carries checks 1, 2 and 6 from ADR 0001.

**Blocked by:** 01

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [x] A pure verdict core takes (base tree, PR diff, ticket file, config, test results) and returns a verdict with reasons.
- [x] Check 1: a test newly skipped, conditionally skipped or marked expected-to-fail → `fail`, naming the test.
- [x] Check 2: a deleted test function, or a test file with fewer assertions than on base → `fail`, naming what was lost.
- [x] Check 6: the PR's ticket file not `done`, or any acceptance box unticked → `fail`.
- [x] A reusable GitHub workflow runs the gate on a PR and posts the verdict and reasons as a PR comment (updated, not duplicated, on re-runs) and as a check status.
- [x] The gate prints no secret and no file contents beyond what reasons require.
- [x] One fixture PR per check proves it fires; one clean fixture proves `pass`. Each fixture test was observed failing before its check existed.

## Comments

Completed first slice of the integrity gate (checks 1, 2, and 6) and reusable workflow:
- Pure `IntegrityCore` (`src/ticket_engine/integrity.py`) evaluates base tree, diff, ticket, config, and test results with zero I/O.
- Check 1 flags newly skipped or xfailed tests (tested in `test_integrity_core.py` and `test_integrity_fixtures.py`).
- Check 2 flags deleted test functions and test files losing assertions.
- Check 6 flags tickets not in `done` status or with unticked acceptance checkboxes.
- `Auto-merge: no` flags ticket for `hold` verdict.
- Thin `GitHubClient` adapter (`src/ticket_engine/github.py`) updates/posts PR comments using HTML marker and sets commit check status.
- Runner adapter (`src/ticket_engine/integrity_runner.py`) connects git and GitHub Actions environment.
- Reusable workflow `.github/workflows/integrity.yml` published for target repos.
