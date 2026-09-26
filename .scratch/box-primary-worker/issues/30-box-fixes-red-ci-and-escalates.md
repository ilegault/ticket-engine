# 30: The box fixes red CI on its own PR, and escalates when it cannot finish

**What to build:**
- **Red CI.** When CI on a box PR is red, the box runs `agy` again on the ticket branch. The prompt is the normal one plus a fixed section listing the names of the failing checks. It does this up to the repo's `max_fix_attempts`; after that the dispatcher's existing escalation (`EscalatePRAction`) is the authority.
- **Escalation.** The box escalates a ticket itself in two cases: the ticket used up `max_resumes_per_ticket` runs (default 3) without reaching `done`, or `agy` kept ending `waiting` past `max_auto_replies` auto-replies. It writes the escalation brief into the ticket file, pushes, opens the PR as a draft labelled `engine:escalated`, and opens one escalation issue.
- **Waiting runs.** A `waiting` run is answered by a fresh run whose prompt adds `dispatch.AUTO_REPLY_TEXT`, exactly as ADR 0004 answers a Jules session.

Spec: `.scratch/box-primary-worker/spec.md` (§Local worker orchestration). ADR 0004, CONTEXT *Escalation*.

**Blocked by:** 29, 26

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in `tests/test_local_worker.py` (the same fakes as tickets 28–29) and `tests/test_github_adapter.py`. `LocalWorker`, `assemble_escalation_brief`, `apply_escalation_to_ticket_text`, `render_escalation_issue` and the parser are real. Write these tests first and watch them fail.

- [ ] **Check runs.** `GitHubClient.list_check_runs(repo, ref) -> list[tuple[str, str]]` returns `(name, conclusion)` pairs from `GET /repos/{repo}/commits/{ref}/check-runs`. There is a recorded-response test.
  - `LocalWorker.fix_ci(entry, ticket, pr_number)` reads the check runs for the PR head. It calls `AgyDriver.start` with the normal prompt plus a section headed `## CI FAILED — FIX IT`, listing one `- <check name>` line per check whose conclusion is `failure`, then pushes.
  - The test asserts the recorded prompt contains `- integrity-gate` for a fake with that check failing, and contains no check that passed.
- [ ] **Waiting gets the auto-reply.** A run whose outcome is `waiting` is followed by a fresh run whose prompt ends with `dispatch.AUTO_REPLY_TEXT`, up to `RepoConfig.max_auto_replies` times per `run_one` call. The next `waiting` escalates with reason `kept_asking`. The test scripts three `waiting` outcomes and asserts two auto-reply prompts, then the escalation.
- [ ] **Resumes run out.** `LocalWorkerConfig.max_resumes_per_ticket` (default 3) bounds the number of `timeout`/`failed` runs in one `run_one` call. When it is reached, the orchestrator:
  1. writes `apply_escalation_to_ticket_text(text, assemble_escalation_brief(...))` into the worktree ticket file;
  2. commits it with message `Escalate <NN>: resumes exhausted`;
  3. pushes;
  4. calls `create_pull_request(..., draft=True)`, unless a PR is open, in which case it calls `convert_pr_to_draft`;
  5. calls `add_issue_labels(..., ["engine:escalated"])`.

  The test asserts all five effects.
- [ ] **One escalation issue.** Each box escalation calls `find_open_issue(repo, "escalation", title)`, then, if that returns `None`, `create_issue` with title and body from `render_escalation_issue`. The reason is `resumes_exhausted` or `kept_asking`, the link is the PR URL, and `owner = repo.split("/")[0]`. A second escalation of the same ticket creates no second issue.
- [ ] **Quota is not a failure.** A `quota` outcome never counts toward `max_resumes_per_ticket` and never escalates. A test with five `quota` outcomes and then `success` asserts no escalation effect was recorded.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
