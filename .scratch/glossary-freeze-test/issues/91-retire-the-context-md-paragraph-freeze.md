# 91: Retire the CONTEXT.md paragraph freeze left over from ticket 43

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Deletes tests:** tests/test_sonnet_fallback_docs.py::test_context_no_other_entries_changed

**Spec:** none; the evidence is below.
**Binding:** ADR 0005 (the Deletes tests line above authorises the one deletion)

**Read first:** `tests/test_sonnet_fallback_docs.py` (whole file, it is short);
`tests/fixtures/sonnet_fallback_docs/context_md_paragraph_hashes.json`.

## Evidence

Ticket 43 added a test that stores a SHA-256 hash of every blank-line-separated
paragraph of `CONTEXT.md`, plus the paragraph count, in
`tests/fixtures/sonnet_fallback_docs/context_md_paragraph_hashes.json`, and fails when
any paragraph other than the two ticket 43 edited differs. Its job was to keep ticket
43's worker inside its two glossary entries. Ticket 43 is merged, so that job is over,
but the test stayed as a permanent freeze on the whole glossary:

- Ticket 90's worker had to regenerate the hash file to land a legitimate glossary edit.
- The developer's commit 95279cc (2026-11-20) appended a new "What a worker reads"
  section to `CONTEXT.md`. No existing paragraph changed, but the paragraph count
  assertion failed, turning `master` red (898 passed, 1 failed) and blocking every open
  PR, including PR 89.

The glossary is protected by the integrity gate (any PR changing `CONTEXT.md` is held
for the developer), so this test adds no protection the gate does not already give. The
other two tests in the file check what ticket 43 actually delivered (the Sonnet wording)
and stay.

## What to build

1. In `tests/test_sonnet_fallback_docs.py`, delete the function named on the
   **Deletes tests:** line above, and only that function. Then delete what only it
   used: the `import hashlib` and `import json` lines, the `HASHES_PATH` constant, and
   the `_EDITED_PARAGRAPH_PREFIXES` tuple with its comment. Keep `CONTEXT_PATH`,
   `_paragraphs`, and the two other test functions byte-for-byte unchanged, with all
   their assertions.
2. Delete the file `tests/fixtures/sonnet_fallback_docs/context_md_paragraph_hashes.json`,
   and the `tests/fixtures/sonnet_fallback_docs/` directory once it is empty.
3. Do not edit `CONTEXT.md`, any ADR, or any other test file.

## Acceptance criteria

The test names in this ticket are written without backticks on purpose: check 9 treats
every backticked test name in a criterion as a new test this PR must add, and this
ticket adds none.

- [ ] The paragraph-freeze test is gone from tests/test_sonnet_fallback_docs.py, and the hashes fixture and its directory are deleted. (no test: the deletion is the change; check 2 verifies it against the Deletes tests line)
- [ ] The other two tests in tests/test_sonnet_fallback_docs.py (the Quota reserve one and the Local worker one) are unchanged and pass when pytest runs that file. (no test: existing tests, run on the touched file)
- [ ] `ruff check .` reports no unused import in tests/test_sonnet_fallback_docs.py. (no test: ruff gate)
- [ ] No other file references context_md_paragraph_hashes.json or HASHES_PATH; `grep -rn "context_md_paragraph_hashes\|HASHES_PATH" src tests scripts` prints nothing. (by hand)
- [ ] The rest of the suite still passes on the current master CONTEXT.md. (no test: CI's full suite)

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q tests/test_sonnet_fallback_docs.py

pytest runs on the touched test file only; CI runs the full suite on the PR. Passing
looks like: ruff clean, the tests-first script exits 0, and pytest reports 2 passed.

## Out of scope

- Regenerating the hash file instead of deleting it. That only postpones the next red
  master to the next glossary edit (the ADR 0015 Jules removal will edit many entries).
- Any other docs-assertion test (`tests/test_phase2_docs.py` and similar). They check
  for named wording, not a whole-file freeze.
- Editing `CONTEXT.md` or the two new ADRs from commit 95279cc.

## Comments
