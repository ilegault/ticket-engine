# 90: A `Deletes tests:` entry without the class name authorises the one test method it names

**Status:** in-progress

**Runner:** any

**Auto-merge:** no

**Blocked by:** None (can start immediately)

**Spec:** none; the evidence is below.
**Binding:** ADR 0005 (decisions 1 and 2), ADR 0001 (check 2), ADR 0013

**Read first:** `src/ticket_engine/integrity.py`: the module docstring, `_ASTVisitor.parse`
(how module-level tests and test methods get their full names) and the Check 2 block in
`IntegrityCore.evaluate` (from `# Check 2: deleted test function` down to the
"lost ... assertion(s)" loop). `tests/test_ticket_quality.py`: the "integrity core: check 2
honours the base ticket's authorisation" section (`evaluate`, `AUTHORISING_TICKET`,
`test_deletion_authorised_on_base_passes_and_says_so`) and the runner section
(`_deletion_repo`, `test_gate_on_real_git_reads_the_authorisation_from_the_base_branch`).
Copy those tests' shape.

## Evidence

RBL ticket 135 (PR 205) escalated on 2026-11-18. Its base copy listed eight deletions as
`tests/test_auto_home.py::<name>`. All eight are methods inside test classes
(`TestRoutineEndToEnd`, `TestStopCodeGate`, `TestMotorTabAutoHome`). `_ASTVisitor` names a
test method `<file>::<Class>::<name>`, and Check 2 compares names exactly, so none of the
eight deletions counted as authorised and the PR failed Check 2. Ticket lint accepted the
entries (`_DELETES_ENTRY_RE` allows both forms), and Check 9 and lint compare bare test names,
so nothing warned the planner. Every document that tells the planner how to write the line
(`AGENTS.md` §10, `docs/agents/issue-tracker.md`, ADR 0005) says `<file>.py::<test>`.

## What to build

After this ticket, a `Deletes tests:` entry of the form `<file>.py::<name>` authorises
deleting a test method named `<name>` in that file when that is the only test method with
that name in the file. The full form `<file>.py::<Class>::<name>` still works as before.
When the short form fits more than one test method, it authorises none of them, and Check 2
says why and how to fix it.

Authorisation still comes only from the base branch's copy of the ticket
(ADR 0005 decision 2). Do not change where `base_ticket` comes from.

1. In `src/ticket_engine/integrity.py`, add a pure module-level function, placed directly
   above `class IntegrityCore`:

       def resolve_authorised_deletions(
           entries: Iterable[str], base_test_names: Iterable[str]
       ) -> tuple[frozenset[str], dict[str, tuple[str, ...]]]:

   It returns `(authorised, ambiguous)`. For each entry, after replacing `\` with `/`:
   - If the entry equals a name in `base_test_names`, that name is authorised. This
     comes first, so a module-level test whose name matches exactly always wins.
   - Otherwise, if the entry has exactly one `::` (`<file>::<name>`), the candidates are the
     base names of the form `<file>::<Class>::<name>` (three parts, same file, last part
     equal to `<name>`). One candidate: it is authorised. Two or more: nothing is
     authorised and `ambiguous[entry]` is the candidates, sorted.
   - Otherwise nothing.
   Import `Iterable` from `collections.abc` next to `Mapping, Sequence`. The function
   takes plain strings and does no I/O (AGENTS.md §3, invariant 1). That is what lets the
   rule be tested from strings.
2. In `IntegrityCore.evaluate`, Check 2 block: replace the `authorised` set comprehension.
   Parse `deletes_tests` from `base_ticket` exactly as now (empty when `base_ticket` is
   falsy), then call `resolve_authorised_deletions(entries, base_tests.keys())`. The
   existing loop, the assertion allowance and the
   `"Check 2: deleted test(s) the ticket authorises on the base branch: "` reason stay as
   they are; they now see full names. After that loop, for each `(entry, candidates)` in
   `sorted(ambiguous.items())` where at least one candidate is not in `head_tests`, set
   `is_failing = True` and append exactly:

       Check 2 fail: Deletes tests entry '<entry>' matches more than one test: <c1>, <c2>. Write the full name of each test to delete.

   with the candidates joined by `", "` in sorted order.
3. In the module docstring of `src/ticket_engine/integrity.py`, under Check 2's ADR 0005
   exception, add one sentence: an entry is a test's full name, or `<file>::<name>` for the
   one test method of that name in the file; an entry that fits several authorises none.
   Name RBL ticket 135 as the reason.
4. In `CONTEXT.md`, replace the body of the **Deletes tests** entry with: the ticket's
   optional `Deletes tests:` line: the test functions the worker may delete, each as its
   pytest node ID (`<file>.py::<name>`, or `<file>.py::<Class>::<name>` for a test method).
   `<file>.py::<name>` also matches a test method when it is the only test in that file
   with that name. Read only from the *base copy* (ADR 0005).
5. In `docs/adr/0005-tickets-must-be-landable-and-holds-are-green.md`, append a section
   `## Amendment, 2026-11-18: entries for test methods` saying, in two or three sentences:
   a test method's entry is its node ID `<file>.py::<Class>::<name>`; `<file>.py::<name>`
   is accepted when exactly one test in the file has that name; when several do, it
   authorises none and Check 2 says so. Name RBL ticket 135. Change nothing else in the ADR.
6. In `docs/agents/issue-tracker.md`, rule 1 under "Writing acceptance criteria": after
   "line listing each one", add "by its pytest node ID (`<file>.py::<Class>::<test>` for
   a test method)".

## Acceptance criteria

Write the tests first in `tests/test_ticket_quality.py`, in the check 2 section after
`test_authorisation_covers_only_the_deleted_tests_own_assertions`, and watch each fail
before changing `src/`. Nothing is faked: the integrity core and the parser are real, and
the runner test uses a real git repo in `tmp_path`. In the fixtures, the deleted test is
named test_old_a and the kept test test_keep, as in the existing section.

- [ ] Test `test_bare_entry_authorises_the_one_test_method_with_that_name`: the base test file has `class TestOld` with a method test_old_a holding two assertions, and a module-level test_keep; the head file has only test_keep; the base ticket's line is `tests/test_26.py::test_old_a`. The verdict is a pass, its reasons include `Check 2: deleted test(s) the ticket authorises on the base branch: tests/test_26.py::TestOld::test_old_a`, and no reason contains "lost".
- [ ] Test `test_bare_entry_matching_two_test_methods_authorises_neither`: the base test file has `class TestA` and `class TestB`, each with a method test_old_a, plus a module-level test_keep; the head file has only test_keep; same base ticket line. The verdict is a fail, its reasons include, exactly once, the step 2 ambiguity message for the entry tests/test_26.py::test_old_a with the candidates tests/test_26.py::TestA::test_old_a and tests/test_26.py::TestB::test_old_a (the test spells out the whole message string), and two reasons equal `Check 2 fail: Deleted test function 'test_old_a' in tests/test_26.py`.
- [ ] Test `test_gate_on_real_git_accepts_a_bare_entry_for_a_test_method`: give `_deletion_repo` two keyword parameters, `base_file: str = BASE_TEST_FILE` and `head_file: str = HEAD_TEST_FILE`, used where it now writes those constants. Build the repo with the class-based base file from the first criterion, the base ticket line `tests/test_26.py::test_old_a`, and a done PR ticket made the way the existing real-git authorisation test makes it (replace ready-for-agent with done and tick the box). `run_integrity_gate` returns 0, and the step summary contains "PASS" and `tests/test_26.py::TestOld::test_old_a`.
- [ ] `CONTEXT.md`, ADR 0005 and `docs/agents/issue-tracker.md` say what steps 4 to 6 say, and nothing else in those files changed (no test: documentation)

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Ticket lint (`ticket_lint.py`) and its messages, and Check 9: unchanged.
- The `bootstrap.py` template text and `AGENTS.md`: unchanged. The developer updates the
  planner wording in `AGENTS.md` §10 by hand.
- Moving `v1`. Target repos see this only after the developer moves the tag.
- RBL ticket 135 itself: its base copy is fixed directly on RBL's `master`.

## Comments

