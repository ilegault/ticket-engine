# 50: A pull request GitHub refuses never crashes the box

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 48 (it edits the same files, `src/ticket_engine/local_worker.py` and `tests/test_local_worker.py`)

**Spec:** none. Background is the box's first real run (tickets 45–47 in this effort).
**Binding:** ADR 0004 (escalation), ADR 0006; ADR 0007 rule 4 (only fixed-template text is posted)

## What to build

Two places in `src/ticket_engine/local_worker.py` call
`GitHubClient.create_pull_request` without handling a refusal:

- `_maybe_open_pull_request`, after agy finishes and the worktree's ticket file
  reads `Status: done`;
- `_escalate`, for the escalation's draft PR.

GitHub answers 422 when, for example, the branch has no commits beyond the base, or
a PR for it already exists. On the box's first real run this happened inside
`_escalate`. The exception propagated out of `BoxLoop.tick` and the box process
died. The escalation issue, the only thing that tells the developer a ticket
needs them, was never opened. `_escalate` also ignores the results of its `git add`
and `git commit`, so a failed escalation commit is invisible and leads straight
into that 422.

After this ticket:

- a refused PR is logged as a WARNING naming the branch and the HTTP code, and the
  box carries on;
- an escalation whose PR is refused still opens its escalation issue, linking to
  the pushed branch instead of a PR;
- a failed escalation `git add` or `git commit` is logged.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests fake
GitHub with `make_fake_github()`, git with `_make_git_runner(...)`, and agy with
`AgyDriver(run_fn=...)`, all from `tests/test_local_worker.py`. Build escalations
the way `test_resumes_exhausted_escalates_with_all_five_effects` does: ticket
`make_ticket(9, effort="phase-1")`, `make_config(max_resumes_per_ticket=3)`, and a
`run_fn` that returns `1, json.dumps({"status": "ERROR", "message": "boom"})`.
A refusal is `create_pull_request.side_effect` set to a real
`urllib.error.HTTPError(url, 422, "Unprocessable Entity", {}, fp)`, where
`fp.read()` returns
`b'{"message":"Validation Failed","errors":[{"message":"No commits between master and ticket/phase-1-09-x"}]}'`.

- [ ] **A refused PR after a finished run is logged, and `run_one` returns.** In
  `_maybe_open_pull_request`, wrap the `create_pull_request` call in
  `try/except urllib.error.HTTPError as exc`. On a refusal, log
  `logger.warning("GitHub refused the PR for %s (HTTP %d); the branch is pushed, the box carries on.", ticket_branch, exc.code)`
  and return. New test `test_refused_pr_after_success_is_logged_and_run_one_returns`
  (caplog at WARNING): `run_fn` returns `0, '{"status": "SUCCESS"}'`,
  `read_ticket_fn` returns a ticket containing `**Status:** done`, and
  `create_pull_request` is refused. `run_one(...)` returns `True` without raising,
  and one WARNING record contains both `GitHub refused the PR for ticket/` and
  `HTTP 422`.
- [ ] **A failed escalation commit is logged, and escalation continues.** In
  `_escalate`, capture `(rc, out)` from the `git add` call and from the
  `git commit` call. When either is non-zero, log
  `logger.warning("Escalation %s for ticket %02d failed (rc=%d): %s", step, ticket.number, rc, out.strip()[-2000:])`,
  with `step` being `"add"` or `"commit"`, and carry on: the push, PR and issue
  steps still run. New test `test_failed_escalation_commit_is_logged`
  (caplog at WARNING): the git runner is scripted with
  `{"commit": (1, "Author identity unknown")}`. One WARNING record contains both
  `Escalation commit for ticket 09 failed (rc=1)` and `Author identity unknown`,
  and `create_issue` is called once.
- [ ] **An escalation whose PR is refused still opens its escalation issue.** In
  `_escalate`, wrap the `create_pull_request` call in
  `try/except urllib.error.HTTPError as exc`. On a refusal:
  - log `logger.warning("GitHub refused the escalation PR for %s (HTTP %d); opening the escalation issue with a branch link.", ticket_branch, exc.code)`;
  - skip `add_issue_labels`;
  - pass `link = f"https://github.com/{entry.repo}/tree/{ticket_branch}"` to
    `render_escalation_issue`. It accepts any `https://github.com/` URL without
    whitespace (see `src/ticket_engine/box_status.py`).

  The `find_open_issue` check before `create_issue` is unchanged. New test
  `test_refused_escalation_pr_still_opens_the_escalation_issue`: with
  `create_pull_request` refused, `run_one(make_repo_entry(repo="owner/repo"), ticket)`
  returns `False` without raising, and `add_issue_labels` is not called.
  `create_issue` is called once, and its body argument (`call_args.args[2]`)
  contains `Link: https://github.com/owner/repo/tree/ticket/phase-1-09-`.
- [ ] **A refusal in box mode does not reach the box loop.** New test
  `test_refused_escalation_pr_in_box_mode_returns_none`: the same setup as the
  previous criterion, called as `run_one(..., box_mode=True)`, returns `None`
  without raising.
- [ ] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are. In particular `test_resumes_exhausted_escalates_with_all_five_effects`
  and `test_escalation_writes_brief_into_the_worktree_copy_of_a_loaded_ticket`
  are untouched. No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Failures of `convert_pr_to_draft`, `add_issue_labels`, `find_open_issue` or
  `create_issue`.
- `fix_ci`.
- Retrying a refused PR on a later tick, or working out why GitHub refused it.
- `GitHubClient` and its logging (ticket 49).

## Comments
