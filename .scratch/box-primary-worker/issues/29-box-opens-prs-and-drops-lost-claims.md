# 29: The box opens its PR and drops a claim it has lost

**What to build:** When a run leaves the worktree's ticket file at `Status: done`, the orchestrator pushes the branch and opens the pull request through the GitHub API: base is the default branch, the title is `<effort>-<NN>: <ticket title>`, and the body is a fixed template. The PR then goes through CI and the integrity gate like any other.

Before every resume, push and PR, the orchestrator re-reads the claim branch. If the branch is gone, or its ticket file says `Claimed-by: jules`, the box has lost the ticket. It pushes nothing more to it, removes its local worktree (the one case where unpushed commits may be discarded, because the ticket is no longer the box's), leaves the remote ticket branch alone, and logs the loss. Spec: `.scratch/box-primary-worker/spec.md` (§Local worker orchestration). ADR 0006 rule 7.

**Blocked by:** 28

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in `tests/test_local_worker.py` (the same fakes as ticket 28) and `tests/test_github_adapter.py` (recorded responses). `LocalWorker` and the parser are real. Write these tests first and watch them fail.

- [ ] **Adapter.** `GitHubClient.create_pull_request(repo, head, base, title, body, draft=False) -> int` posts to `POST /repos/{repo}/pulls` and returns the PR number. `GitHubClient.find_open_pr(repo, head_branch) -> int | None` uses `GET /repos/{repo}/pulls?state=open&head=<owner>:<branch>`. There is one recorded-response test for each.
- [ ] **PR only when done.** After a run whose worktree ticket file (read through `read_ticket_fn` and parsed with `TicketParser`) has status `done`, and after the push, `create_pull_request` is called once. Its arguments are `head=ticket/<effort>-<NN>-<slug>`, `base=<RepoConfig.default_branch>`, `title="<effort>-<NN>: <title>"`, and body exactly `Ticket <NN> worked by the box.\n\nTicket file: <ticket path>\nBranch: <branch>`.
  - If `find_open_pr` already returns a number, no second PR is created.
  - A run leaving status `in-progress` creates no PR.
  - Three tests.
- [ ] **Lost claim: gone.** Before resuming a claimed ticket, the orchestrator calls `list_claim_branches`. If `claim/<effort>/<NN>` is absent, it records no push and no PR, runs `git worktree remove --force <path>` (the only permitted `--force`), and `run_one` returns `False`. The log record contains `lost claim`.
- [ ] **Lost claim: Jules holds it.** Same as above, when the claim branch's ticket file parses with `claimed_by == "jules"`. A separate test, same assertions.
- [ ] **No push after loss mid-run.** If the claim disappears while `agy` is running (the fake GitHub client's claim list changes between the timer pusher's first and second checks), the pusher stops pushing. Its check calls the same `claim_still_mine()` helper before every push. The test asserts exactly one push was recorded.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
