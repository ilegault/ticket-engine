# 76: Ticket lint: every criterion names a test or carries a tag

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 75

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0013 rule 2; ADR 0005

## What to build

Check 9 (ticket 74) is only as strong as the tickets: a criterion that names no
test proves nothing. After this ticket, ticket lint
(`lint_ticket` in `src/ticket_engine/ticket_lint.py`) refuses a `ready-for-agent`
ticket with any unticked acceptance criterion that names no test, unless the
criterion is tagged `(by hand)` or `(no test: <reason>)`. The dispatcher already
refuses to start a ticket with lint findings, and `dispatch --dry-run` lists them
under "Ticket problems", so a planner sees every gap before handing off.

The criteria are the `- [ ]` blocks under `## Acceptance criteria` (or, without
that heading, every box before `## Comments`), wrapped lines joined as
`_blocks` already does. A block names a test when `_TEST_IDENT_RE` matches it.

## Acceptance criteria

Write the tests first, in `tests/test_ticket_quality.py` beside the existing lint
tests, and watch each fail before changing `src/`. Tickets are built from text with
`TicketParser().parse_text`, as those tests do. Nothing is faked.

- [ ] **A criterion with no named test is a finding.** Test `test_lint_flags_a_criterion_with_no_named_test`: one criterion names `test_a`, another names nothing; asserts exactly one finding, reading `criterion "<first 60 chars>" names no test: name the test that proves it, or tag it (by hand) or (no test: <reason>)`.
- [ ] **The two tags are accepted.** Test `test_lint_accepts_by_hand_and_no_test_tags`: criteria tagged `(by hand)` and `(no test: doc-only)`; asserts no finding.
- [ ] **A fully named ticket is clean.** Test `test_lint_accepts_a_ticket_whose_every_criterion_names_a_test`.
- [ ] **Only `ready-for-agent` tickets and unticked boxes are judged.** Test `test_lint_ignores_ticked_boxes_and_other_statuses`: a `done` ticket with an unnamed criterion, and a `ready-for-agent` ticket whose only unnamed box is ticked; asserts no finding for either.
- [ ] **The dry run shows it.** Test `test_dispatch_dry_run_lists_a_criterion_with_no_named_test`: a temporary repo with `.ticket-engine.toml` and one such ticket; `run_dispatch_dry_run` output contains the finding under "Ticket problems" (copy the existing dry-run test in `tests/test_cli.py`).

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
