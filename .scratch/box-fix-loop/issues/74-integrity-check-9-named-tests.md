# 74: Integrity check 9: every named test exists and failed on the old code

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0013; ADR 0001

## What to build

Check 6 asks only that the ticket is `done` with every box ticked, and the worker
ticks the boxes. Check 7 asks only that new tests fail on the base code, and one
trivial test satisfies it. A worker once marked a ticket done having built almost
none of it.

After this ticket the integrity gate has **check 9**: every test a ticket's
acceptance criteria name (a backticked `test_...`) must exist on the PR's head and
must be one of the new tests check 7 saw fail on the base code. A criterion that
rewrites an existing test only needs that test to exist. A missing or
never-failing named test is a `fail`, so the worker must fix it.

In `src/ticket_engine/integrity.py`:

- A new pure `named_test_problems(raw_ticket: str, head_test_names: set[str], failed_on_base_names: set[str] | None) -> list[str]`.
  It reads the same criteria lines `IntegrityCore._check_acceptance_criteria`
  reads (under `## Acceptance criteria` when that heading exists, otherwise every
  box before `## Comments`), joining a criterion's wrapped lines the way
  `ticket_lint._blocks` does. For each backticked `test_\w+` name in a criterion:
  absent from `head_test_names` → `Check 9 fail: criterion "<first 60 chars>" names
  \`<name>\`, which is not in the PR's tests`; present, not in
  `failed_on_base_names`, and the criterion does not contain `rewrite`, `rewrites`
  or `rewritten` (any case) → `Check 9 fail: \`<name>\` (criterion "<first 60
  chars>") did not fail on the base code, so it does not prove the criterion`.
  When `failed_on_base_names` is `None` (check 7 did not run), only presence is
  checked.
- `IntegrityCore.evaluate` calls it after check 7, only when check 6 found exactly
  one ticket and it is `done`. Names are test function names (the `func_name` of the
  `_TestFunctionInfo` entries on the head, and the last `::` part of check 7's
  failed qualified names). Any problem sets the verdict to `fail` with one reason
  per problem.
- The pass summary `All integrity checks passed (checks 1-7)` becomes
  `All integrity checks passed (checks 1-9)`.

## Acceptance criteria

Write the tests first, in a new `tests/test_integrity_check9.py`, and watch each
fail before changing `src/`. `named_test_problems` tests are pure. `evaluate` tests
build base and head trees and `base_test_results` the way
`tests/test_integrity_check7.py` does; nothing is faked.

- [ ] **A missing named test fails.** Test `test_check9_fails_when_a_named_test_is_missing`: a done ticket names `test_alpha` and `test_beta`, the head has only `test_alpha`; asserts verdict `fail` and a reason naming `test_beta`.
- [ ] **A named test that passed on the old code fails.** Test `test_check9_fails_when_a_named_test_passed_on_base`: `test_alpha` is new but not in `failed_tests`; asserts `fail` and a reason containing `did not fail on the base code`.
- [ ] **A rewrite criterion needs only an existing test.** Test `test_check9_rewrite_criterion_needs_only_an_existing_test`: a criterion "Rewrite `test_old` in place …" with `test_old` present on base and head and not failing on base; asserts no check 9 reason.
- [ ] **No named tests, no check 9.** Test `test_check9_silent_when_the_ticket_names_no_tests`: asserts the verdict and reasons equal what they were without check 9 (`pass`, summary `checks 1-9`).
- [ ] **Every named test that did its job passes.** Test `test_check9_passes_when_every_named_test_is_new_and_failed_on_base`: two named tests, both new and both in `failed_tests`; asserts verdict `pass`.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
