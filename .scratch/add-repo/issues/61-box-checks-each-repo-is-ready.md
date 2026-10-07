# 61: The box checks each repo is ready (token, config, environment) and publishes the ones that are not

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 58, 60, 70, 80

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0010 rules 2, 4 and 5; ADR 0009 rule 6; ADR 0007 rule 4

## What to build

Before the box claims anything in a listed repo, it checks the repo is *ready*:
cloned, reachable with the box's token, carrying a `.ticket-engine.toml`, and with
a built repo environment. A repo failing a check is *not ready*: no new claims
there, and the box status issue lists it with a reason code, which lets Jules
overflow onto it (ticket 56). The decision of which reason applies is a **pure
function in `box_core.py`**, so the order of checks is tested without git, GitHub
or subprocesses. The baseline check and alerts come in ticket 62.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. `BoxCore` tests
are pure. `BoxLoop` tests use `make_loop`, `make_github()` (set
`can_read_variables.return_value`), a fake `git_runner`, and monkeypatch
`ticket_engine.box_worker.ensure_repo_env` with a fake returning an `EnvResult`.
Real `.ticket-engine.toml` files are written into clone folders under `tmp_path`.

- [ ] **The readiness rule is pure.** In `src/ticket_engine/box_core.py` add
  `@dataclass(frozen=True) class RepoFacts` (`cloned: bool`, `token_ok: bool`,
  `has_engine_config: bool`, `env_ok: bool`, `baseline_passed: bool | None = None`)
  and `repo_readiness(facts: RepoFacts) -> NotReadyReason | None`, checking in
  this order: `clone_failed`, `no_token_access`, `no_engine_config`, `env_failed`,
  `baseline_red` (only when `baseline_passed is False`; None means not checked).
  Test `test_repo_readiness_reports_the_first_failing_check` covers each reason
  with every later check also failing, and the all-ready case.
- [ ] **The loop gathers the facts each tick.** In `BoxLoop._build_world`, for each
  entry accepting new work: `cloned` is the outcome of ticket 58's clone step
  (a failed clone now produces a not-ready repo instead of only a warning);
  `token_ok = self.github_client.can_read_variables(entry.repo)`;
  `has_engine_config = (Path(entry.path) / ".ticket-engine.toml").is_file()` after
  the pull; `env_ok` comes from
  `ensure_repo_env(entry.repo, Path(entry.path), repo_config, self.config.envs_dir, default_command_runner, os.name == "nt").ok`,
  called only when the earlier checks pass. A not-ready repo is built with
  `accepting_new=False`, so it still resumes its own claims. Tests
  `test_repo_without_engine_config_is_not_ready_and_gets_no_claim`,
  `test_repo_the_token_cannot_reach_is_not_ready`,
  `test_failed_environment_makes_the_repo_not_ready`,
  `test_ready_repo_is_claimed_from_as_before`.
- [ ] **A failed environment is not rebuilt every tick.** The loop records the
  failing fingerprint in `box_readiness.json` in `logs_dir`
  (`{"<repo>": {"env_failed_fingerprint": "<hex>"}}`) and does not call
  `ensure_repo_env` again for that repo while `env_fingerprint(...)` still equals
  it. Test `test_env_failure_is_retried_only_when_the_fingerprint_changes`
  (edit `requirements.txt` in the clone between ticks; assert the fake was called
  once, then twice).
- [ ] **The status issue lists not-ready repos.** `_current_box_status` passes
  `not_ready=tuple(NotReady(repo, reason) ...)` in repo-list order. Test
  `test_status_body_lists_not_ready_repos` asserts the body sent to
  `update_issue_body` contains `Not ready: o/a (no_engine_config)` and no other
  text from the repo (no paths, no command output).
- [ ] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are (they use `repo_list_fn=None`, which skips readiness checks: keep it
  that way). No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Running the baseline and raising or closing alerts (ticket 62).
- Any change to how the dispatcher reads the status (ticket 56 did that).

## Comments
