# 44: Integrity gate check 8 — a PR that creates a new ticket file always holds

**What to build:** Add check 8 to the integrity gate: a PR that adds a ticket
file which did not exist on the base branch always holds, unconditionally,
naming the new path(s) in the reason. Spec: `.scratch/ticket-creation-hold/spec.md`.

This ticket also touches `docs/adr/` and `CONTEXT.md` (governance and glossary),
so it is always held by check 4 regardless of check 8's own verdict — expected,
not a bug.

**Blocked by:** None (can start immediately)

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

Write the new tests first and watch them fail before touching `integrity.py`.

- [x] **`get_diff_added_paths` helper.** In `src/ticket_engine/integrity.py`, add a
  new function `get_diff_added_paths(pr_diff: str | Mapping[str, str], base_tree:
  Mapping[str, str]) -> set[str]`, placed immediately after `get_diff_changed_paths`
  (before `get_diff_added_lines_with_locations`). Mapping mode: return
  `{p.replace("\\", "/") for p in pr_diff if p not in base_tree}`. String mode:
  split `pr_diff` into per-file chunks the same way `parse_and_apply_diff` already
  does (split on lines starting `diff --git `), and for each chunk read the `--- `
  and `+++ ` lines the same way `get_diff_changed_paths` does (stripping the `a/`
  and `b/` prefixes); a chunk whose `--- ` path is `/dev/null` and whose `+++ `
  path is not `/dev/null` contributes that `+++ ` path (with backslashes
  normalized to `/`) to the result.
- [x] **Check 8 in `IntegrityCore.evaluate`.** Immediately after the existing
  `# 7. Check test results if provided` block and before `# 9. Check auto-merge
  flag`, add:
  ```python
  # 8. Check 8: PR creates a new ticket file -> always hold (ADR 0001, ADR 0008).
  added_paths = get_diff_added_paths(pr_diff, base_tree)
  new_ticket_files = sorted(p for p in added_paths if _TICKET_FILE_RE.match(p))
  if new_ticket_files:
      is_holding = True
      reasons.append(
          "Check 8 hold: PR creates new ticket file(s), which always need the "
          f"developer's own merge: {', '.join(new_ticket_files)}"
      )
  ```
  Do not change verdict precedence: `is_failing` still wins over `is_holding`
  exactly as it already does for every other check.
- [x] **Docstring.** In `integrity.py`'s module docstring, add a line to the
  "Checks implemented" list, after the existing Check 7 line: `- Check 8: PR adds
  a ticket file absent from the base branch -> hold, naming it. Fires
  independently of every other check (ADR 0001 §2.8, ADR 0008).`
- [x] **New tests in `tests/test_integrity_core.py`** (add near the end of the
  file), styled like `test_check_4_touching_github_produces_hold` (a real string
  diff, not the `head_tree` Mapping convention used by the Check 6 tests):
  - `test_check_8_new_ticket_file_produces_hold`: `base_tree = {"tests/test_sample.py":
    "def test_a(): assert True\n"}`; `pr_diff` is a string with a `diff --git a/.scratch/e/issues/50-new.md
    b/.scratch/e/issues/50-new.md`, `new file mode 100644`, `--- /dev/null`,
    `+++ b/.scratch/e/issues/50-new.md`, and a hunk adding `# 50: New`, `**Status:**
    done`, `## Acceptance criteria`, `- [x] All done`; `ticket` is that same text
    parsed with `TicketParser().parse_text(...)`. Assert `verdict.verdict ==
    Verdict.HOLD` and `any("Check 8 hold" in r and ".scratch/e/issues/50-new.md" in r
    for r in verdict.reasons)`.
  - `test_check_8_editing_an_existing_ticket_file_is_not_flagged`: `base_tree`
    includes `".scratch/e/issues/50-t.md": "# 50: T\n**Status:** in-progress\n##
    Acceptance criteria\n- [ ] x\n"` alongside a test file; `pr_diff` is a string
    diff with `--- a/.scratch/e/issues/50-t.md` / `+++ b/.scratch/e/issues/50-t.md`
    changing the status line and ticking the box; `ticket` is the resulting `done`
    text. Assert `not any("Check 8" in r for r in verdict.reasons)`.
- [x] **Fix the two existing tests that would otherwise now trip check 8**, in
  `tests/test_integrity_core.py`. Neither test's expected verdict or existing
  assertions change — only their setup data gains one line each, since both
  represent a worker editing its *own already-existing* ticket but only ever put
  that ticket's path into `head_tree`, never `base_tree`:
  - `_eval_ticket`'s shared helper: before building `head_tree`, add
    `base_tree[".scratch/e/issues/35-t.md"] = "# 35: T\n\n**Status:**
    ready-for-agent\n\n**What to build:** a thing.\n"` to its `base_tree` dict.
  - `test_check_6_ticket_marked_done_with_a_test_change_is_not_flagged`: before
    building `head_tree`, add `base_tree[".scratch/e/issues/38-t.md"] = "# 38:
    T\n**Status:** in-progress\n## Acceptance criteria\n- [ ] Built it\n"` to its
    `base_tree` dict.
- [x] **Fixture pair.** Add `tests/fixtures/integrity/check8_new_ticket/`, laid
  out like `tests/fixtures/integrity/check4_hold/`:
  - `base/tests/test_sample.py`: `def test_one():\n    assert 1 + 1 == 2\n`
  - `pr.diff`: a real unified diff (`diff --git a/.scratch/e/issues/50-new.md
    b/.scratch/e/issues/50-new.md`, `new file mode 100644`, `--- /dev/null`, `+++
    b/.scratch/e/issues/50-new.md`, a hunk adding a done ticket with a ticked box)
  - `ticket.md`: the same done-ticket text, matching `check4_hold/ticket.md`'s
    header style (`# 50: Check 8 new-ticket fixture`, `**Status:** done`,
    `**Blocked by:** None`, `**Runner:** any`, `**Auto-merge:** yes`, `##
    Acceptance criteria`, `- [x] All done`)

  Add `test_fixture_check8_new_ticket_holds` to `tests/test_integrity_fixtures.py`
  (near `test_fixture_check4_protected_path_holds`), loading this fixture and
  asserting `verdict.verdict == Verdict.HOLD` and `any("Check 8 hold" in r for r
  in verdict.reasons)`.
- [x] **New ADR.** Create `docs/adr/0008-a-new-ticket-file-always-holds.md` with
  exactly this content:

  ```markdown
  # ADR 0008 — A new ticket file always holds

  **Status:** accepted
  **Date:** 2026-09-27
  **Applies to:** the integrity gate
  **Amends:** ADR 0001 (adds check 8)

  ## Context

  Ticket text becomes an unattended worker's literal instructions: a worker reads
  its ticket file and acts on whatever it says, with `--dangerously-skip-permissions`
  and nobody watching. The gate's other checks prove a PR is *procedurally* honest —
  tests pass, nothing was muted, the boxes are ticked — but none of them read what a
  ticket asks for. A PR that adds one brand new ticket file, marks it `done`, ticks
  its own boxes, and ships a trivial passing test would sail through every check
  exactly like an ordinary worker updating its own ticket's status, because check 6
  only holds when a PR changes *more than one* ticket file (a planning rewrite) or
  *none* (an unrelated infra change) — a single new ticket is neither.

  That is the one place outside content becomes a future agent's actual prompt: a
  worker, or in this public repo anyone who can get a PR merged, that can slip a new
  ticket file past the gate plants instructions a later, equally unattended worker
  will read and carry out as if the developer had planned it.

  ## Decision

  **Check 8: a PR that adds a ticket file which did not exist on the base branch
  always holds**, whatever every other check says. `IntegrityCore.evaluate` names
  each new path under `.scratch/<effort>/issues/*.md` in the hold reason. This is
  unconditional: `Auto-merge: yes`, a clean suite, and every acceptance box ticked
  make no difference. Only the developer's own merge lets a new ticket into the
  tracker.

  A worker editing its **own existing** ticket — flipping `Status:` to `done`,
  ticking a box, adding a dated line under `## Comments` — is unaffected. Only a
  path absent from the base branch counts as new; check 8 never looks at what
  changed inside a file that was already there.

  ## Consequences

  - Every ticket a worker's PR proposes now needs the developer's own click, the
    same as a protected-path change (check 4). That is deliberate: unlike a status
    update, a new ticket is a new instruction for the next unattended run to carry
    out.
  - A planning PR that adds several tickets at once already held under check 6; it
    now also holds under check 8, with both reasons listed. Harmless and redundant.
  - This does not catch a worker rewriting the body of a ticket it is not assigned
    to, or smuggling instructions into a comment on an existing ticket. Both stay
    out of scope: check 8 only ever looks at whether the *path* is new.
  ```

- [x] **Amend ADR 0001's header and check list**, in
  `docs/adr/0001-auto-merge-on-green-behind-an-integrity-gate.md`. Change the
  `**Status:**` line from:
  ```
  **Status:** accepted; amended by ADR 0005 (check 2 honours a ticket's
  `Deletes tests:` line from the base branch; a `hold` reports green and the developer
  merges it by hand)
  ```
  to (append a clause, keep the ADR 0005 clause unchanged):
  ```
  **Status:** accepted; amended by ADR 0005 (check 2 honours a ticket's
  `Deletes tests:` line from the base branch; a `hold` reports green and the developer
  merges it by hand); amended by ADR 0008 (check 8: a PR that creates a new ticket
  file always holds)
  ```
  Then, in the Decision section's numbered check list (currently ending at item
  7, "the PR's new tests fail when run against the base branch's code..."), add
  an item 8 immediately after item 7:
  ```
     8. no new ticket file (one absent from the base branch) is added by the PR ->
        `hold`, always, whatever the other checks say.
  ```
- [x] **CONTEXT.md glossary.** Immediately after the existing `**Merge hold**`
  entry (and before `**Waiting session**`), insert this new entry:
  ```
  **New ticket file** — a ticket markdown file the PR adds that did not exist on
  the base branch. Check 8 holds any PR that creates one, always, whatever every
  other check says: the developer merges it by hand. A worker editing its own
  existing ticket's `Status:` or `## Comments` is not affected.

  ```
  (Keep the existing blank-line spacing between glossary entries.)

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`,
`pytest -q`.

## Comments

2026-09-27: Implemented check 8 in `IntegrityCore.evaluate` (`src/ticket_engine/integrity.py`), a pure `get_diff_added_paths` helper, ADR 0008, the ADR 0001 amendment, and the CONTEXT.md glossary entry.
- Added tests `test_check_8_new_ticket_file_produces_hold` and `test_check_8_editing_an_existing_ticket_file_is_not_flagged` in `tests/test_integrity_core.py`; both watched red before the check existed.
- Fixed `_eval_ticket` and `test_check_6_ticket_marked_done_with_a_test_change_is_not_flagged` to seed `base_tree` with the ticket's pre-PR content, since check 8 now reads a path missing from `base_tree` as new.
- Added the `check8_new_ticket` fixture pair and `test_fixture_check8_new_ticket_holds` in `tests/test_integrity_fixtures.py`.
- Full suite green: `ruff check .`, `scripts/check_tests_first.py`, `pytest -q`.
