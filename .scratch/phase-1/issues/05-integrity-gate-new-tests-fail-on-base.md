# 05: Integrity gate, check 7: new tests must fail on the base code

**What to build:** The gate proves, mechanically, that a PR's new tests actually test the new behaviour: each new test is run against the base branch's source, and one that already passes there flags the PR.

**Blocked by:** 03

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [x] The gate identifies test functions that are new in the PR (not merely edited).
- [x] It runs exactly those tests with the PR's test files but the base branch's source, using the test command from the repo config.
- [x] Any new test that passes on base → `hold`, listing the tests (hold, not fail: refactor tickets may add characterisation tests that already pass).
- [x] A new test that errors on base because the thing it tests does not exist yet counts as failing on base (the desired outcome).
- [x] No new tests in a PR that touches source → this check is silent (the tests-first gate covers that case).
- [x] Proven on a tiny fixture project: one PR whose new test fails on base (pass), one whose new test passes on base (hold).
- [x] Runtime of the check is reported in the gate output.

## Comments

Built Check 7 of the integrity gate:
- Pure core `find_new_test_functions` extracts new AST test function qualified names not present in base tree.
- Adapter `temporary_base_source` context manager swaps source paths to `base_ref` and `execute_new_tests_on_base` runs each new test using configured test command, measuring runtime.
- Any new test that passes on base produces a `hold` verdict listing the passing tests (ADR 0001 §2.7). If a test errors or fails on base, that is the desired outcome (pass). Check is silent if no new tests exist.
- Tested across unit tests (`test_integrity_core.py`), fixtures (`test_integrity_fixtures.py`), and a real tiny git fixture project end-to-end (`test_integrity_check7.py`). All 98 tests pass.
