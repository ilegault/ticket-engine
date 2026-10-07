# 70: Fix attempts are counted durably, and running out escalates the ticket

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0011 rule 1; ADR 0006; ADR 0007 rules 4 and 5

## What to build

Today the box counts fix attempts in `BoxLoop._fix_attempts`, a dict in memory
(`src/ticket_engine/box_worker.py`). A restart forgets the count, and when the count
reaches `max_fix_attempts` the box simply stops choosing `FixCI` for that PR and
nothing else happens: the PR sits red forever while the status issue says
`working`. This is what stalled Slackbot PR #125.

After this ticket the count lives in a file, a quota pause never counts, and a PR
whose fix attempts have run out is escalated exactly like a ticket whose resumes
ran out.

- A new ledger `fix_attempts.json` in `logs_dir`, keyed `"<owner/repo>#<pr number>"`,
  value `{"attempts": <int>, "escalated": <bool>}`. Read and written the way
  `BoxLoop._read_ledger` / `_append_ledger` handle `box_ledger.json` (missing or
  unreadable file → empty). The `_fix_attempts` dict is removed.
- `BoxPR` (`src/ticket_engine/box_core.py`) gains `escalated: bool = False`.
  `BoxLoop._collect_open_prs` fills `fix_attempts` and `escalated` from the ledger.
- A new box step `EscalateFixes(repo: str, ticket_number: int, pr_number: int)`.
  In `BoxCore._next_step`, rule 3 becomes: for a box-claimed ticket whose open PR
  has `ci_failed` and is not `escalated`, return `FixCI` while
  `fix_attempts < max_fix_attempts`, otherwise `EscalateFixes`. An escalated PR
  gets neither.
- `BoxLoop._do_fix_ci` adds 1 to the PR's count in the ledger **before** calling
  `fix_ci`, and takes it back when the result's `outcome` is `"quota"`.
- `BoxLoop` carries out `EscalateFixes` by calling a new
  `LocalWorker.escalate_fixes(entry, ticket, pr_number) -> None`, then marks the
  PR `escalated` in the ledger. `escalate_fixes` makes (or reuses) the worktree the
  way `fix_ci` does with `_create_worktree`, then calls the existing `_escalate`
  with `reason=EscalationReason.ci_failed`. In `_escalate`, the commit message for
  `ci_failed` is `Escalate <NN>: fix attempts exhausted` (today every reason other
  than `resumes_exhausted` says `kept asking`).

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. `BoxCore` tests
are pure (`make_world` and `ticket` in `tests/test_box_core.py`). Box loop tests use
`make_loop`, `make_github()` and the recording `FakeWorker` in
`tests/test_box_worker.py`; add an `escalate_fixes` recorder to `FakeWorker`. Local
worker tests use the fakes of `tests/test_local_worker.py`. The ledger file is real,
under `tmp_path`.

- [ ] **Out of attempts escalates.** Tests `test_fix_attempts_at_budget_escalate_instead_of_fixing` (a `BoxPR` with `ci_failed=True, fix_attempts=3`, `max_fix_attempts=3` → `EscalateFixes` for that repo, ticket and PR) and `test_escalate_fixes_beats_claiming_a_new_ticket` (same world plus a claimable frontier ticket → still `EscalateFixes`).
- [ ] **An escalated PR is left alone.** Test `test_escalated_pr_gets_neither_fix_nor_escalation`: `BoxPR(ci_failed=True, fix_attempts=3, escalated=True)` → the step is whatever rules 4–6 give, never `FixCI` or `EscalateFixes`.
- [ ] **The count survives a restart.** Test `test_fix_attempt_count_survives_a_new_loop`: a red box PR; two ticks on one `make_loop`, then a second `make_loop` on the same `logs_dir` ticks twice more. Asserts `fix_ci` was called exactly 3 times in total and the fourth tick called `escalate_fixes` once.
- [ ] **Quota does not count.** Test `test_quota_fix_run_does_not_count_as_an_attempt`: `FakeWorker.fix_ci_result` has `outcome="quota"` on the first call and `"success"` after; asserts the ledger's `attempts` for that PR is 1 after two fix runs, not 2.
- [ ] **The escalation has all five effects.** Test `test_escalate_fixes_has_all_five_effects_and_says_fix_attempts_exhausted`, copied from `test_resumes_exhausted_escalates_with_all_five_effects`: brief written into the worktree ticket, commit message `Escalate 07: fix attempts exhausted`, push, `convert_pr_to_draft` on the open PR, label `engine:escalated`, and one escalation issue whose body carries reason `ci_failed`.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
