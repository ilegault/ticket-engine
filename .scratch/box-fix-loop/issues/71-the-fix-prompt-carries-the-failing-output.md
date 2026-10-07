# 71: A fix run gets the fix prompt with the real failing output, and goes through outcome handling

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 69, 70

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0011 rules 1 and 2; ADR 0004; ADR 0007 rules 4 and 5

## What to build

A fix run today (`LocalWorker.fix_ci` in `src/ticket_engine/local_worker.py`) sends
agy the full implement-from-scratch prompt (`assemble_prompt`) plus a list of
failing check *names*. That prompt tells agy to check the frontier, run the claim
check and set `in-progress`, on a ticket that is already `done` and already claimed,
so agy can reasonably stop without doing anything. And a fix run that fails or times
out is returned raw, skipping `_resolve_outcome`, so it is silently dropped.

After this ticket a fix run gets a **fix prompt** that says plainly what the
situation is and carries the actual failing output, and it is resumed, paused or
escalated like any other run.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Prompt tests are
pure. Local worker tests use the fakes in `tests/test_local_worker.py` (fake agy
driver, `_make_git_runner`, a fake GitHub client) plus a fake command runner passed
through a new `LocalWorker.__init__` parameter
`command_runner: CommandRunner = default_command_runner`. GitHub adapter tests use
recorded responses like the existing `list_check_runs` test in
`tests/test_github_adapter.py`.

- [ ] **The fix prompt.** New pure `assemble_fix_prompt(repo: str, ticket_path: str, failures: list[tuple[str, str]]) -> str` in `src/ticket_engine/prompt.py`. It contains, in order: the repo and ticket path; the heading `## THIS TICKET IS ALREADY YOURS AND DONE` with the sentences "The ticket is claimed by you and its Status is done. Do not run scripts/check_claimable.py, do not create or change any claim, and do not change the Status line. Fix only the failures below, then commit."; `_UNATTENDED_RULE`; the sentence "Never delete, skip, xfail or weaken a test, and never raise a ratchet, to make a check pass."; then `## CI FAILED — FIX IT` and, per failure, `### <name>` followed by its output in a fenced block. It does not include the ticket skill. Tests `test_fix_prompt_says_the_ticket_is_already_yours_and_done`, `test_fix_prompt_carries_each_failing_output_under_its_name`, `test_fix_prompt_has_no_claim_instructions` (asserts `### Claim the ticket` is absent).
- [ ] **GitHub gives the failing job's log.** `GitHubClient.list_failed_check_runs(repo, ref) -> list[tuple[str, int]]` returns `(name, id)` for each check run on `ref` whose conclusion is `failure` (same endpoint as `list_check_runs`). `GitHubClient.get_job_log_tail(repo, job_id, max_chars=4000) -> str` calls `GET /repos/{repo}/actions/jobs/{job_id}/logs` (GitHub answers with a redirect to a plain-text log, which `urllib` follows) and returns the last `max_chars` characters, or `""` on any HTTP or network error. A GitHub Actions check run's id is its job id. Tests `test_list_failed_check_runs_returns_names_and_ids` and `test_get_job_log_tail_returns_the_tail_and_empty_on_error`.
- [ ] **`fix_ci` sends the real output.** `fix_ci` builds `failures` from (a) every failed check run: `(name, get_job_log_tail(...))`, and (b) a local `run_gate` (ticket 69) of the repo's `gate_commands` in the worktree, env `self._env_for(entry)` merged with the repo's `test_env`: `("local: " + command, output_tail)` for each failed command. It starts agy with `assemble_fix_prompt(...)`. Test `test_fix_ci_prompt_has_job_log_tail_and_local_gate_failure`: the fake GitHub returns one failed check `lint` with a log ending `F401 unused import`, the fake command runner fails `ruff check .` with `E501`; asserts the prompt agy received contains `### lint`, `F401 unused import`, `### local: ruff check .` and `E501`, and no passing check's name.
- [ ] **Fix runs go through outcome handling.** `fix_ci` passes agy's result to `_resolve_outcome(..., box_mode=True)`. `_resolve_outcome` gains `resume_prompt: str | None = None`; when given, `timeout`/`failed` resumes start agy with that prompt instead of `_assemble_checkpoint_prompt`, and auto-replies start agy with `resume_prompt + "\n\n" + AUTO_REPLY_TEXT`. `fix_ci` passes its fix prompt. Test `test_failed_fix_run_is_resumed_with_the_fix_prompt_then_escalated`: agy scripted `failed` three times; asserts the second and third agy prompts equal the first (the fix prompt) and the resumes-exhausted escalation's five effects happened.
- [ ] **A quota fix run is returned to the box.** Test `test_quota_fix_run_returns_the_quota_result`: agy scripted `quota`; `fix_ci` returns a result whose `outcome` is `"quota"` (so ticket 70's ledger takes the attempt back) and the worktree is left in place.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
