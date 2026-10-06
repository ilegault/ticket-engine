# 47: The box resumes its own claim, and never runs agy without a worktree

**What to build:** Two failures from the box's second real run on Slackbot
ticket 98, after tickets 45 and 46 landed.

1. A local branch `ticket/buyer-picker-lists-buyers-98-…` was left in the box's
   clone by the earlier failed run. `LocalWorker._create_worktree` ran
   `git worktree add <path> -b <branch> origin/<claim>`, git refused ("a branch
   named … already exists"), the failure was only logged as a warning, and
   `run_one` started agy with `cwd` set to a folder that did not exist
   (`NotADirectoryError: [WinError 267]`). The tick crashed.
2. That run had already created the claim branch with `Claimed-by: box`. On the
   next tick `BoxCore` rule 4 returns `ResumeClaim`, but `run_one` always starts
   by creating the claim branch, reads GitHub's "already exists" as a collision
   with someone else, and returns `False`. Rule 4 then picks the same step again
   with no wait, so the loop spins against the GitHub API. The box could never
   resume its own claim, including after any quota pause.

`fix_ci` had the same missing-folder problem: a successful run removes its
worktree, and `fix_ci` started agy in that removed path.

**Blocked by:** 45, 46

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

- [x] **`run_one` resumes the box's own claim.** In `src/ticket_engine/local_worker.py`,
  when `create_claim_branch` returns `False`, `run_one` reads the claim branch's
  ticket file with the new `LocalWorker._claim_owner(repo, claim_branch, ticket_path)`.
  `"box"` → log `Resuming the box's own claim …` and continue without re-committing
  `Claimed-by`. Anything else, including an unreadable file (`None`) → log and
  return `False` as before. Tests:
  `test_run_one_resumes_its_own_claim_when_the_claim_branch_already_exists`
  (agy runs once, `commit_file_change` not called) and
  `test_run_one_skips_a_claim_branch_claimed_by_jules`. The existing
  `test_run_one_skips_when_claim_already_exists` (no `Claimed-by` line) still
  passes unchanged.
- [x] **Worktrees are reused, pruned and reset, or the run stops.**
  `_create_worktree` reuses an existing folder that has a `.git` entry (no
  `worktree add`). Otherwise it runs `git worktree prune`, and a fresh claim uses
  `worktree add <path> -B <ticket branch> origin/<claim branch>`, so a stale local
  branch is reset to the claim head. A non-zero `worktree add` raises the new
  `WorktreeError` carrying git's output. `run_one` catches it, logs it at ERROR,
  and returns `False` without starting agy or pushing. Tests:
  `test_failed_worktree_add_never_starts_agy` and
  `test_existing_worktree_folder_is_reused_not_re_added`.
  `test_run_one_git_arg_order_for_fresh_claim` was rewritten in place (same name)
  to assert `-B` and no `-b`.
- [x] **`fix_ci` makes its worktree first.** `fix_ci` calls `_create_worktree`
  before agy. A `WorktreeError` is logged and `fix_ci` returns `None` without
  starting agy. Tests: `test_fix_ci_makes_the_worktree_before_running_agy`
  (worktree add on `origin/<ticket branch>` happens before agy) and
  `test_fix_ci_never_starts_agy_without_a_worktree`.
- [x] **A no-op claim or resume does not spin the loop.** In
  `src/ticket_engine/box_worker.py`, when `run_one` returns `False` for a
  `ClaimTicket` or `ResumeClaim`, the new `BoxLoop._wait_after_no_op` logs
  `<step> did nothing; waiting N minutes before the next tick` at WARNING and
  sleeps `poll_interval_minutes`. Tests:
  `test_a_resume_that_does_nothing_waits_a_poll_interval`,
  `test_a_claim_that_does_nothing_waits_a_poll_interval`, and
  `test_a_claim_that_worked_does_not_add_a_wait`.
- [x] **Docstrings say why.** `local_worker.py`'s module docstring gains a
  `Ticket 47` section, and `box_worker.py`'s operating notes gain the no-op wait.
  No existing test was deleted, skipped or weakened.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`,
`pytest -q`.

## Comments

2026-10-06: Receipt. The developer asked the planner session to fix this directly
rather than queue it, so this ticket records work already done. The tests above
were written first and seen to fail (6 red before the change; the two `fix_ci`
tests red with the `fix_ci` change reverted), then the change was made. Checked
in a Python 3.12 copy of the repo: `ruff check .` clean and `pytest -q` 628
passed (619 before, plus 9 new). `scripts/check_tests_first.py` was not run,
because that copy had no git history; the change touches both `src/` and `tests/`.
A real-git check confirmed `worktree add -B` succeeds over an existing local
branch of the same name.
