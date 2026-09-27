# Spec: A new ticket file always holds (integrity gate check 8)

**Status:** ready-for-agent

Governing decisions: `docs/adr/0001-auto-merge-on-green-behind-an-integrity-gate.md`
(amended by this effort's ADR 0008). Glossary: `CONTEXT.md` (Integrity gate, Verdict,
Merge hold). Ticket numbers for this effort continue from 44.

## Problem Statement

Check 6 holds a PR that changes more than one ticket file (a planning rewrite) or
none (an unrelated infra change), but judges a PR that changes exactly one ticket
file as an ordinary worker updating its own ticket. A PR that instead **creates**
one brand-new ticket file — marks it `done`, ticks its own boxes, ships a trivial
passing test — looks exactly like that ordinary case and auto-merges.

Ticket text becomes an unattended worker's literal instructions (it runs with
`--dangerously-skip-permissions`, nobody watching). A new ticket file slipped past
the gate is a new instruction a later, equally unattended worker will read and
carry out as if the developer had planned it — the one place outside content
becomes a future agent's actual prompt, in a public repo. The developer wants any
PR that creates a ticket file to always require his own merge, the same way a
protected-path change already does under check 4.

## Solution

A new integrity check, check 8: a PR that adds a ticket file which did not exist
on the base branch always holds, unconditionally, whatever every other check
says. It fires independently of check 6 and never turns a hold into a fail or a
fail into a hold on its own (verdict precedence, ADR 0001, is unchanged: fail
still beats hold).

## Implementation Decisions

- **New pure helper `get_diff_added_paths`** in `src/ticket_engine/integrity.py`,
  next to `get_diff_changed_paths`: paths present in the PR but absent from
  `base_tree`. Mapping mode (`pr_diff` given as a full tree dict, the test
  convention used throughout `tests/test_integrity_core.py`) returns
  `{p for p in pr_diff if p not in base_tree}`. String mode (a real unified diff,
  what production always passes) parses each file's `diff --git` chunk and reports
  a path as added only when its old side is `/dev/null` — the same marker
  `parse_and_apply_diff` and `get_diff_changed_paths` already rely on for deletions
  and renames, so this introduces no new assumption about git's diff format.
- **Check 8 lives in `IntegrityCore.evaluate`**, filtering `get_diff_added_paths`'s
  result through the existing `_TICKET_FILE_RE` (`^\.scratch/[^/]+/issues/[^/]+\.md$`)
  and setting `is_holding = True` with a reason naming every new path, unconditionally
  — it does not read ticket status, acceptance boxes, or anything else about the new
  file's content. This is deliberate: a new ticket holds because it is new, not
  because of what it says.
- **Production needs no wiring changes.** `integrity_runner.run_integrity_gate`
  already passes `base_tree` and the real `pr_diff` string into `core.evaluate`;
  check 8 reads both exactly as they already arrive. No new CLI flag, no new
  adapter input.
- **This does not touch `ticket_lint.py`.** Lint only reports mistakes a worker
  can fix by rewriting its own ticket (ADR 0005 §5: undeclared test deletions, a
  malformed `Deletes tests:` entry, missing acceptance boxes). A new-ticket hold
  is not something any rewrite avoids, so it is not a lint finding.

## Testing Decisions

- Two new tests in `tests/test_integrity_core.py`, styled like the existing
  `test_check_4_touching_github_produces_hold` (a real string diff, not the
  Mapping convention): one whose diff adds a new `.scratch/e/issues/50-new.md`
  with `new file mode` / `--- /dev/null` and asserts `Verdict.HOLD` with a
  `"Check 8 hold"` reason naming the path; one whose diff edits an *existing*
  ticket path (present in `base_tree` with different content) and asserts no
  `"Check 8"` reason appears.
- A new fixture pair `tests/fixtures/integrity/check8_new_ticket/` (`base/`,
  `pr.diff`, `ticket.md`), following the same layout as `check4_hold/`, plus one
  test in `tests/test_integrity_fixtures.py` loading it and asserting `HOLD`.
- **Existing-test fix, not a weakening.** `tests/test_integrity_core.py`'s
  `_eval_ticket` helper and `test_check_6_ticket_marked_done_with_a_test_change_is_not_flagged`
  both put a ticket key only into `head_tree`, never into `base_tree` — under the
  Mapping-mode semantics check 8 needs, that reads as "this ticket is new" even
  though the tests mean "the worker is updating its own, already-existing ticket."
  Both must also seed `base_tree` with the ticket's pre-PR content (any distinct
  placeholder text at the same path) so check 8 does not fire for them. This adds
  one line of setup data to each; it changes no assertion and no test's expected
  verdict.
- Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`,
  `pytest -q`.

## Out of Scope

- Editing the *body* of an existing ticket (smuggling instructions into its own
  text, or into a `## Comments` line) is not caught by check 8, which looks only
  at whether the path is new. Not addressed here.
- No change to check 6, `ticket_lint.py`, the dispatcher, or any target-repo
  wiring.
- No secret-scanning of diffs or commit messages (the other mitigation raised
  alongside this one). Not addressed here; a separate effort if wanted.

## Further Notes

This is the one place outside content becomes a future agent's actual prompt,
raised while reviewing this repo's public-repo exposure. The developer approved
check 8 as the fix, in preference to (or in addition to, later) a broader
secret-scanning check.
