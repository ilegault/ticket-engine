# 62: The box runs each repo's baseline, and raises one alert per not-ready repo

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 61, 69, 70, 80

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0010 rules 4 and 5; ADR 0007 rules 4 and 5

## What to build

A ready-looking repo can still have a red test suite on the box before any ticket
touches it, which would cost three fix attempts per ticket and trip the circuit
breaker. The box runs the repo's own `gate_commands` (the *baseline*) on its
default branch, inside the repo environment, and treats a red baseline as not
ready (`baseline_red`). The result is cached, so the baseline reruns only when the
default branch or the environment changes. Separately, the box opens one
fixed-template alert issue per not-ready repo and closes it when the repo is ready
again.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests use the
same fakes as ticket 61, plus a fake command runner for the gate commands
(monkeypatch `ticket_engine.box_worker.default_command_runner`) and a fake
`git_runner` that answers `rev-parse HEAD` with a scripted SHA.

- [ ] **The baseline runs when the default branch or environment changed.** After
  the environment is ready, the loop gets `HEAD` with
  `["git", "-C", path, "rev-parse", "HEAD"]` and compares `(sha, env fingerprint)`
  with the entry stored under the repo in `box_readiness.json`
  (`"baseline": {"sha": ..., "fingerprint": ..., "passed": bool}`). When they
  differ it runs `run_gate(repo_config.gate_commands, path, env)` from
  `src/ticket_engine/gate.py` (ticket 69; every command runs, none is skipped after
  a failure), with `env` = `env_vars(...)` merged with the repo's `test_env`, stores
  the result, and passes `baseline_passed` into `RepoFacts`. When they match it reuses
  the stored result without running anything. Tests
  `test_baseline_runs_once_per_head_and_fingerprint`,
  `test_red_baseline_makes_the_repo_not_ready`,
  `test_new_head_reruns_a_red_baseline_and_clears_it_when_green`.
- [ ] **Its output stays in the log.** A red baseline logs
  `logger.warning("baseline red for %s at %s:\n%s", repo, sha[:7], format_gate_report(result)[-4000:])`.
  Nothing from the output reaches GitHub. Test
  `test_baseline_output_never_reaches_a_github_request_body` (copy the approach of
  `test_agy_failure_text_never_reaches_a_github_request_body`).
- [ ] **A time limit.** `LocalWorkerConfig` gains
  `baseline_timeout_minutes: int = 30` (read by `load_local_config`). Each gate
  command runs with that timeout; a timeout counts as red. Add
  `baseline_timeout_minutes = 30` to the `toml` block in `docs/box-setup.md` so
  `test_config_example_is_real` still passes. Test
  `test_local_config_reads_baseline_timeout`.
- [ ] **One alert per not-ready repo, closed when ready.** In `_write_status`, after
  updating the status body, for each repo in this tick's entries: if not ready,
  render `render_repo_not_ready_alert(repo, reason, owner, since)` and create it
  with label `engine:box-alert` unless `find_open_issue(engine_repo, "engine:box-alert", title)`
  already finds it; if ready and that issue is open, close it. `since` is when the
  box first saw the repo not ready this run. Tests
  `test_not_ready_repo_opens_one_alert_across_ticks` and
  `test_alert_closes_when_the_repo_becomes_ready`.
- [ ] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are. No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- The rest of the runbook (ticket 67).
- Running baselines in parallel with agy.

## Comments
