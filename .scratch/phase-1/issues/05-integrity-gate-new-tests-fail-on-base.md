# 05: Integrity gate, check 7: new tests must fail on the base code

**What to build:** The gate proves, mechanically, that a PR's new tests actually test the new behaviour: each new test is run against the base branch's source, and one that already passes there flags the PR.

**Blocked by:** 03

**Status:** in-progress

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] The gate identifies test functions that are new in the PR (not merely edited).
- [ ] It runs exactly those tests with the PR's test files but the base branch's source, using the test command from the repo config.
- [ ] Any new test that passes on base → `hold`, listing the tests (hold, not fail: refactor tickets may add characterisation tests that already pass).
- [ ] A new test that errors on base because the thing it tests does not exist yet counts as failing on base (the desired outcome).
- [ ] No new tests in a PR that touches source → this check is silent (the tests-first gate covers that case).
- [ ] Proven on a tiny fixture project: one PR whose new test fails on base (pass), one whose new test passes on base (hold).
- [ ] Runtime of the check is reported in the gate output.

## Comments
