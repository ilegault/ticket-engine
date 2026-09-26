# 29: The box opens its PR and drops a claim it has lost

**What to build:** When a run leaves the worktree's ticket file at `Status: done`, the orchestrator pushes the branch and opens the pull request through the GitHub API: base is the default branch, the title is `<effort>-<NN>: <ticket title>`, and the body is a fixed template. The PR then goes through CI and the integrity gate like any other.

Before every resume, push and PR, the orchestrator re-reads the claim branch. If the branch is gone, or its ticket file says `Claimed-by: jules`, the box has lost the ticket. It pushes nothing more to it, removes its local worktree (the one case where unpushed commits may be discarded, because the ticket is no longer the box's), leaves the remote ticket branch alone, and logs the loss. Spec: `.scratch/box-primary-worker/spec.md` (§Local worker orchestration). ADR 0006 rule 7.

**Blocked by:** 28

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in `tests/test_local_worker.py` (the same fakes as ticket 28) and `tests/test_github_adapter.py` (recorded responses). `LocalWorker` and the parser are real. Write these tests first and watch them fail.

- [x] **Adapter.** `GitHubClient.create_pull_request(repo, head, base, title, body, draft=False) -> int` posts to `POST /repos/{repo}/pulls` and returns the PR number. `GitHubClient.find_open_pr(repo, head_branch) -> int | None` uses `GET /repos/{repo}/pulls?state=open&head=<owner>:<branch>`. There is one recorded-response test for each.
- [x] **PR only when done.** After a run whose worktree ticket file (read through `read_ticket_fn` and parsed with `TicketParser`) has status `done`, and after the push, `create_pull_request` is called once. Its arguments are `head=ticket/<effort>-<NN>-<slug>`, `base=<RepoConfig.default_branch>`, `title="<effort>-<NN>: <title>"`, and body exactly `Ticket <NN> worked by the box.\n\nTicket file: <ticket path>\nBranch: <branch>`.
  - If `find_open_pr` already returns a number, no second PR is created.
  - A run leaving status `in-progress` creates no PR.
  - Three tests.
- [x] **Lost claim: gone.** Before resuming a claimed ticket, the orchestrator calls `list_claim_branches`. If `claim/<effort>/<NN>` is absent, it records no push and no PR, runs `git worktree remove --force <path>` (the only permitted `--force`), and `run_one` returns `False`. The log record contains `lost claim`.
- [x] **Lost claim: Jules holds it.** Same as above, when the claim branch's ticket file parses with `claimed_by == "jules"`. A separate test, same assertions.
- [x] **No push after loss mid-run.** If the claim disappears while `agy` is running (the fake GitHub client's claim list changes between the timer pusher's first and second checks), the pusher stops pushing. Its check calls the same `claim_still_mine()` helper before every push. The test asserts exactly one push was recorded.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

**2026-09-26**: Implemented.

- `GitHubClient.create_pull_request` and `GitHubClient.find_open_pr` added to
  `src/ticket_engine/github.py`, with recorded-response contract tests in
  `tests/test_github_adapter.py` (fixtures `pull_create_success.json`,
  `pulls_open_list.json`, `pulls_open_list_empty.json`).
- `LocalWorker` gained `_maybe_open_pull_request` (checked after every push, in
  both the normal run path and the quota-resume path), `_claim_still_mine`
  (the single `list_claim_branches` + `Claimed-by` check used before every
  push and before opening a PR), `_push_if_claimed` (the push wrapper every
  push — timer and final — now goes through), and `_force_remove_worktree`
  (the one place `--force` is passed, for a worktree the box has lost).
- `_handle_quota_error` now checks `_claim_still_mine` before resuming; on
  loss it force-removes the worktree, logs "Lost claim for ticket ...", and
  returns `False` without pushing or sleeping.
- `make_fake_github()` in `tests/test_local_worker.py` now defaults
  `list_claim_branches` to a stand-in that reports every claim branch as
  present (existing tests build the fake before they know the ticket/effort),
  so tests exercising a lost claim set a real list explicitly.
- Six new tests in `tests/test_local_worker.py` (PR-only-when-done x3,
  lost-claim-gone, lost-claim-jules, no-push-after-mid-run-loss) and three in
  `tests/test_github_adapter.py`; one existing test
  (`test_run_one_commits_claimed_by_to_claim_branch`) relaxed from
  `assert_called_once` to "every call used the claim branch ref", since
  `claim_still_mine` now also reads that file before each push.
- Gates: `ruff check .` clean; `python scripts/check_tests_first.py` OK;
  `pytest -q` — 486 passed, 0 failed.
