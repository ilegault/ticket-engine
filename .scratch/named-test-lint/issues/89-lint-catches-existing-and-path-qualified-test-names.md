# 89: Ticket lint catches a criterion that names an existing test or a path-qualified test

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** none; the evidence is below.
**Binding:** ADR 0013 (rule 2), ADR 0014 (decision 3), ADR 0005

**Read first:** `src/ticket_engine/ticket_lint.py` (module docstring, `lint_ticket`,
`lint_tickets`, `_criterion_blocks`); `src/ticket_engine/integrity.py` (`_TEST_NAME_RE`,
`_REWRITE_WORD_RE`, `_ASTVisitor`, `find_new_test_functions`, `named_test_problems`);
`src/ticket_engine/cli.py` (`run_dispatch_dry_run`); `tests/test_ticket_quality.py` and
`tests/test_cli.py` for the existing lint and dry-run tests to copy.

## Evidence

RBL ticket 87 escalated on 2026-11-17 (RBL PR 179). One criterion mentioned an
existing test in backticks as an example to copy:
"A rig test (prior art: `test_charge_text_matches_the_faraday_tab_after_an_insertion_closes`) ...".
Lint counted that as the criterion naming its test, so the dry run was clean. Check 9
then required that existing test to fail on the base code, which it never can, so the
work, though correct, could not land. Separately, RBL ticket 94 wrote two criteria as
`tests/test_video_view.py::test_press_on_draft_grabs_instead_of_drawing`. Neither lint
nor check 9 reads that form (`_TEST_NAME_RE` needs a bare `test_...` between the
backticks), so lint said "names no test" and gave no hint why.

## What to build

After this ticket, `dispatch --dry-run <clone>` lists both mistakes under "Ticket
problems", with a message that says how to fix each, before any agent claims the ticket.

1. In `src/ticket_engine/integrity.py`, move the nested `is_test_file` out of
   `find_new_test_functions` into a module-level `_is_under_test_paths(path: str,
   test_paths: Sequence[str]) -> bool` with the same behaviour, and call it from
   `find_new_test_functions`. Add a pure public
   `collect_test_names(files: Mapping[str, str], test_paths: Sequence[str] = ("tests",)) -> frozenset[str]`:
   for each `(path, content)` that `_is_under_test_paths` accepts, parse it with
   `_ASTVisitor` and collect every `_TestFunctionInfo.func_name` (module-level tests and
   test methods in classes). This makes lint and check 9 agree on what counts as a
   test, from a single definition.
2. In `src/ticket_engine/ticket_lint.py`:
   - `lint_ticket(ticket: Ticket, existing_tests: frozenset[str] | None = None) -> list[str]`
     and `lint_tickets(tickets: Sequence[Ticket], existing_tests: frozenset[str] | None = None) -> dict[int, list[str]]`
     (passes `existing_tests` through). Both stay pure.
   - New module regex `_PATH_QUALIFIED_RE = re.compile(r"`([\w./-]+\.py::(?:\w+::)?(test_\w+))`")`.
   - Import `_REWRITE_WORD_RE` from `ticket_engine.integrity` (do not copy it), so the
     rewrite wording that excuses a test in check 9 is the same wording lint accepts.
   - For each unticked criterion block (the loop that already emits "names no test"):
     a. For each `_PATH_QUALIFIED_RE` match, emit
        `criterion "<criterion[:60]>" names `<whole match>`: check 9 reads only a bare backticked test name, so write `<test name>` and give the file in words`.
        When a criterion has a path-qualified match and no bare `_TEST_IDENT_RE` match,
        emit this finding instead of the "names no test" finding, never both.
     b. When `existing_tests` is not None, for each bare `_TEST_IDENT_RE` name in the
        criterion that is in `existing_tests`, and only when the criterion does not match
        `_REWRITE_WORD_RE`, emit
        `criterion "<criterion[:60]>" names `<name>`, which already exists, so it cannot fail on the base code and check 9 will fail the PR: name the new test that proves it, say the criterion rewrites `<name>`, or move `<name>` out of the criterion`.
   - Extend the module docstring's numbered rules with these two (5 and 6) and a WHY
     line naming RBL ticket 87.
3. In `src/ticket_engine/cli.py`, `run_dispatch_dry_run` reads every `.py` file under
   each of `config.test_paths` in the clone (paths relative to the clone, `/`
   separators, UTF-8, skipping files it cannot decode), passes the mapping to
   `collect_test_names`, and calls `lint_tickets(tickets, existing_tests=...)`. The file
   reading stays in `cli.py`: lint and integrity stay pure (AGENTS.md §3, invariant 1),
   which is what lets the new rules be tested from plain strings.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tickets are built from
text with `TicketParser().parse_text`, as the existing lint tests do. Nothing is faked;
the dry-run test uses a real temporary repo. Fixture test names (test_old, test_new,
test_x and so on) are written here without backticks on purpose: check 9 treats every
backticked test name in a criterion as a test this PR must add.

- [x] Test `test_lint_flags_a_criterion_naming_an_existing_test` in `tests/test_ticket_quality.py`: a `ready-for-agent` ticket whose only criterion names an existing test (fixture name test_old, backticked in the fixture text), linted with existing_tests holding test_old, gives exactly one finding, equal to the 2b message for test_old; the same ticket linted with `existing_tests=None` gives no finding.
- [x] Test `test_lint_accepts_an_existing_test_the_criterion_rewrites` in `tests/test_ticket_quality.py`: a criterion that says it rewrites test_old in place (fixture text: Rewrite, then test_old in backticks, then in place), linted with existing_tests holding test_old, gives no finding.
- [x] Test `test_lint_flags_a_path_qualified_test_name` in `tests/test_ticket_quality.py`: a criterion whose only test reference is the path-qualified tests/test_x.py::test_new (backticked in the fixture text) gives exactly one finding, equal to the 2a message for that match and test_new, and no "names no test" finding.
- [x] Test `test_collect_test_names_reads_module_and_class_tests` in `tests/test_integrity_check9.py`: collect_test_names given a tests/test_a.py module (a module-level test_x, a class TestY with method test_z, and a plain helper) and a src/test_q.py module (a test_q), with test_paths ("tests",), returns exactly test_x and test_z.
- [x] Test `test_dispatch_dry_run_lists_a_criterion_naming_an_existing_test` in `tests/test_cli.py`: a temporary repo with `.ticket-engine.toml`, a tests/test_old_file.py defining test_old, and one `ready-for-agent` ticket whose criterion names test_old in backticks; `run_dispatch_dry_run` returns 0 and its output has the 2b finding under "Ticket problems".

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- `DispatchCore`, `box_core` and `live_dispatch` keep calling `lint_ticket` / `lint_tickets`
  without `existing_tests`. Giving the live dispatcher the clone's test names is a
  separate ticket.
- Check 9 itself (`named_test_problems`) does not change: it still reads only bare
  names. This ticket makes lint warn about what check 9 will refuse.
- No ticket in any target repo is rewritten here.

## Comments

2026-11-20: Implemented `collect_test_names` (sharing `_is_under_test_paths` and `_ASTVisitor` with Check 9) in `integrity.py`, added existing-test and path-qualified-test checks to `lint_ticket` / `lint_tickets` in `ticket_lint.py`, and wired `run_dispatch_dry_run` in `cli.py` to collect test names from `config.test_paths`. Covered by all five named tests in `tests/test_ticket_quality.py`, `tests/test_integrity_check9.py`, and `tests/test_cli.py`; `ruff check .`, `check_tests_first.py`, and the 10-file test selection (272 tests) passed.

CI red from master, not this ticket: `tests/test_sonnet_fallback_docs.py::test_context_no_other_entries_changed`. First red at `95279cc` (`new adrs`, direct push to `master`). Re-run CI once `master` is green: `gh run rerun 44787217112`.

