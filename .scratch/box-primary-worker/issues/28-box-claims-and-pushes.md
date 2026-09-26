# 28: The box claims any ticket and never loses pushed work

**What to build:** `LocalWorker` works any ticket, not only `Runner: windows` ones.
- **Claiming.** It creates the claim branch, commits `**Claimed-by:** box` into the ticket file on it, and creates the ticket branch `ticket/<effort>-<NN>-<slug>` from the claim branch's head. If that ticket branch already exists on the remote, the worker resumes from it instead.
- **Pushing.** `agy` only commits. The orchestrator pushes the ticket branch every `checkpoint_push_minutes` while `agy` runs, and again after every run, whatever the outcome. It authenticates with the token through per-process git configuration, never through git config on disk.
- **Worktrees.** A worktree with unpushed commits is never removed.
- **Ticket skill.** Section 6b is rewritten to one consistent rule: commit at every criterion boundary, never push, never open a PR.

Spec: `.scratch/box-primary-worker/spec.md` (§Local worker orchestration). ADR 0006 rules 2 and 4, ADR 0007 rule 3.

**Blocked by:** 19, 21

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in `tests/test_local_worker.py`, using a fake `git_runner` that records argument lists and environments and returns scripted results, a fake GitHub client that records calls, and a fake `AgyDriver` (`run_fn`). The pusher's timer takes an injectable `clock`/`sleep`. `LocalWorker`, `assemble_prompt` and the parser are real. Write these tests first and watch them fail.

- [ ] **Claim with Claimed-by, then branch from it.** `run_one(entry, ticket)` calls `create_claim_branch`, then commits the ticket file on `claim/<effort>/<NN>` with `**Claimed-by:** box` inserted after the `Status:` line. Reuse the helper ticket 21 added for the Jules path; do not write a second one. It then creates the worktree with `git worktree add <path> -b ticket/<effort>-<NN>-<slug> origin/claim/<effort>/<NN>` after a `git fetch origin claim/<effort>/<NN>`.
  - If `git ls-remote --heads origin ticket/<effort>-<NN>-<slug>` returns a line, the worktree is instead added on `origin/ticket/...` with `--track`, and no new branch is created.
  - Assert the recorded git argument lists in order.
- [ ] **Push after every run, and on a timer.** After each `AgyDriver.start` returns, whatever the outcome (`success`, `quota`, `timeout`, `failed`), the orchestrator runs `git -C <worktree> push origin HEAD:refs/heads/ticket/<effort>-<NN>-<slug>`. While `agy` runs, a background pusher runs the same command every `LocalWorkerConfig.checkpoint_push_minutes` (default 20).
  - Test the pusher on its own, with a fake clock, as `CheckpointPusher(push_fn, interval_s, sleep_fn).run_until(stop_event)`. It pushes at 0, 20 and 40 minutes and stops when the event is set.
  - A failed push is logged and does not stop the run.
- [ ] **Token never on disk or in logs.** Every push passes the environment variables `GIT_CONFIG_COUNT=1`, `GIT_CONFIG_KEY_0=http.https://github.com/.extraheader` and `GIT_CONFIG_VALUE_0=AUTHORIZATION: basic <base64 of x-access-token:TOKEN>` through the git runner's environment argument; extend the runner signature to `(args, cwd, env)`.
  - No git argument list contains the token.
  - A test with token `tok-123` asserts `tok-123` and its base64 form are absent from every recorded argument list and from every captured log record (`caplog`).
- [ ] **Never remove a worktree with unpushed work.** Before any `git worktree remove`, the orchestrator runs `git -C <worktree> rev-list --count origin/ticket/...@{u}..HEAD` or the equivalent `status -sb` check. It removes only when the count is `0` and the worktree is clean. Otherwise it keeps the worktree and logs the path. `--force` is never passed. A test with the fake returning `2` asserts no remove call was recorded.
- [ ] **Skill section 6b is consistent.** In `src/ticket_engine/resources/ticket_skill.md` §6b:
  - "commit and push the work in progress" becomes "commit the work in progress";
  - the bullet reads "**Do not push or open PRs yourself.** The local worker pushes your commits and opens the PR.";
  - the `--continue` sentence becomes "it will start a fresh session on the same branch that includes your last progress note."

  §7's local-worker push and `gh pr create` steps get a first line: "The box's local worker does steps 3–5 for you; skip them when running under it." A test asserts §6b contains no occurrence of `push the work` and none of `--continue`. `run_one` works a ticket of any runner. `run()` and `find_windows_frontier` keep their current behaviour, so their existing tests pass unchanged. The box loop (ticket 31) picks tickets through `BoxCore`, not through them.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
