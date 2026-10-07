# 72: Pre-push gate: the box runs the repo's gate commands before opening or updating a PR

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 71

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0011 rule 3; ADR 0007 rules 4 and 5; ADR 0010 rule 2

## What to build

Nothing on the box checks the work before it reaches GitHub. agy's own local gate
is trusted, and on Slackbot PR #125 it let two unused imports through to CI.

After this ticket the box runs the **pre-push gate** itself: the repo's
`gate_commands`, every one of them, in the worktree, inside the repo environment.
It runs before the box opens a PR and before every push to a ticket branch that
already has an open PR. Red means no PR and no push: agy is sent back with every
failure, and that run counts as a resume. Work-in-progress pushes to a branch with
no PR (the `CheckpointPusher` timer, a run that ended before the ticket is `done`)
are not gated, since nothing can merge them.

In `src/ticket_engine/local_worker.py`:

- A new `LocalWorker._publish(entry, ticket, effort, ticket_path, ticket_branch, claim_branch, worktree_path, push_env) -> GateResult | None`.
  If the worktree ticket file is not `done` and the branch has no open PR
  (`github_client.find_open_pr`), it pushes as today and returns `None`. Otherwise
  it runs `run_gate(repo_config.gate_commands, worktree_path, env, self._command_runner)`
  with `env` = `self._env_for(entry)` merged with the repo's `test_env`. Green: push
  through `_push_if_claimed`, then `_maybe_open_pull_request`, and return the
  result. Red: push nothing, open nothing, log `pre-push gate red for <repo> #<NN>`
  plus `format_gate_report(...)[-4000:]` at warning level, and return the result.
- Every place that today calls `_push_if_claimed` then `_maybe_open_pull_request`
  after an agy run (the end of `run_one`, the quota, waiting and resume branches of
  `_resolve_outcome`, and `fix_ci`) calls `_publish` instead. The
  `CheckpointPusher` timer keeps calling `_push_if_claimed` directly, but skips the
  push while the branch has an open PR.
- In `_resolve_outcome`, a run whose agy result is a success but whose `_publish`
  returned a red result is treated like a `failed` run: it counts one resume
  against `max_resumes_per_ticket` (exhausting it escalates `resumes_exhausted`
  as today), and the next run's prompt is the previous prompt plus
  `"\n\n## PRE-PUSH GATE FAILED — FIX IT\n"` and `format_gate_report(result)`.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests use the
fakes in `tests/test_local_worker.py`, the fake command runner added in ticket 71,
and a real ticket file in the worktree under `tmp_path` whose `Status:` the fake
agy run sets to `done`.

- [ ] **Red gate, no PR.** Test `test_red_gate_opens_no_pull_request`: agy succeeds and marks the ticket done; `ruff check .` fails; asserts `create_pull_request` was never called and no `git push` ran after the run.
- [ ] **Red gate, no push to an open PR.** Test `test_red_gate_does_not_push_to_a_branch_with_an_open_pr`: `find_open_pr` returns 125 and the gate is red; asserts no `git push`. Test `test_checkpoint_push_without_pr_is_ungated`: no open PR, ticket not done; asserts the push runs and the command runner was never called.
- [ ] **agy goes back with every failure, and it counts.** Test `test_red_gate_sends_agy_back_with_every_failure_and_counts_a_resume`: two gate commands fail on the first run and pass on the second; asserts the second agy prompt contains `## PRE-PUSH GATE FAILED — FIX IT` and both commands' failing output, and the PR opens after the second run. Test `test_red_gate_every_run_escalates_when_resumes_run_out`: the gate stays red; asserts the resumes-exhausted escalation's five effects after `max_resumes_per_ticket` runs.
- [ ] **Green gate changes nothing.** Test `test_green_gate_pushes_and_opens_the_pr`: asserts the push and `create_pull_request` happen exactly as before this ticket.
- [ ] **Gate output stays local.** Test `test_gate_output_never_reaches_a_github_request_body`, copying `test_agy_failure_text_never_reaches_a_github_request_body` in `tests/test_box_worker.py`: a failing command's output carries a marker string; asserts no recorded GitHub call argument contains it.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
