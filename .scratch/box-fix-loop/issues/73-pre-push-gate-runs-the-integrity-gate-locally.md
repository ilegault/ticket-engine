# 73: Pre-push gate: the integrity gate runs locally, writing nothing to GitHub

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 72

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0011 rule 3; ADR 0001; ADR 0005

## What to build

Ticket 72's pre-push gate catches the repo's own checks. An integrity-gate `fail`
(a newly skipped test, a deleted test, a ticket box left unticked, a new test that
passes on the old code) still only shows up on GitHub and costs a fix attempt.
After this ticket the pre-push gate ends with the integrity gate, run locally
against the default branch, writing nothing to GitHub. A local `fail` blocks the
push like a red command; a `hold` does not, because a hold is a green check that
waits for the developer.

Check 7 runs the PR's new tests with the Python running the gate
(`execute_new_tests_on_base` uses `sys.executable -m pytest`), so the local run must
use the repo environment's Python. The engine has no dependencies
(`dependencies = []` in `pyproject.toml`), so it runs from that Python with the
engine's source folder on `PYTHONPATH` instead of being installed into every repo
environment.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Integrity runner
tests use `tests/test_integrity_runner.py`'s real-git fixtures and patch
`ticket_engine.integrity_runner.GitHubClient` with a recorder. Local worker tests
use the ticket 72 fakes.

- [x] **`--local` writes nothing to GitHub.** `integrity_runner.main` gains `--local`: it calls `run_integrity_gate` with `token=None`, `repo_name=None`, `pr_number=None` and `head_sha=None` whatever the environment holds, and prints `format_verdict_comment(verdict)` to stdout. Tests `test_local_flag_makes_no_github_calls_even_with_a_token_in_the_environment` (`GITHUB_TOKEN`, `PIPELINE_TOKEN` and `GITHUB_REPOSITORY` set; asserts the recorder was never constructed) and `test_local_flag_prints_the_verdict_comment`.
- [x] **The pre-push gate runs it in the repo environment.** After every gate command passes, `_publish` fetches the default branch (`git -C <worktree> fetch origin <default_branch>`) and runs, through `self._command_runner` with `shell=False`, `[<repo env python>, "-m", "ticket_engine.integrity_runner", "--local", "--repo-path", <worktree>, "--base-ref", "origin/<default_branch>"]`. `<repo env python>` is `env_paths(self.config.envs_dir, entry.repo, os.name == "nt").python`. The env is the gate's env plus `PYTHONPATH` set to the engine's source folder, `str(pathlib.Path(ticket_engine.__file__).resolve().parents[1])`. Test `test_local_integrity_runs_with_the_repo_env_python_and_engine_on_pythonpath` asserts the exact argument list and the `PYTHONPATH` value.
- [x] **A local `fail` blocks the push.** Exit code 1 is a failed gate entry named `integrity gate (local)` with its stdout as output. Test `test_local_integrity_fail_blocks_the_push_and_reaches_the_prompt`: asserts no push and that the next agy prompt contains `integrity gate (local)` and the verdict text.
- [x] **A local `hold` still pushes.** Exit code 0 (a `pass` or a `hold`) counts as passed. Test `test_local_integrity_hold_still_pushes`: the fake runner answers exit 0 with a hold verdict comment; asserts the push and the PR happen.
- [x] **Not run when a command already failed.** Test `test_local_integrity_skipped_when_a_gate_command_failed`: `ruff check .` fails; asserts the integrity runner command was never run.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments

2026-10-08: `integrity_runner.main` gains `--local` (token, repo, PR number and sha forced to `None`, no event payload read). `LocalWorker._publish` now ends a green gate with `_with_local_integrity`: fetch `origin/<default>`, then the repo-env Python runs `ticket_engine.integrity_runner --local` (`shell=False`, engine source on `PYTHONPATH`) as an entry named `integrity gate (local)`. Exit 0 (pass or hold) passes; any other exit blocks the push and its output reaches the next prompt.
Tests: criterion 1 `test_local_flag_makes_no_github_calls_even_with_a_token_in_the_environment`, `test_local_flag_prints_the_verdict_comment`; 2 `test_local_integrity_runs_with_the_repo_env_python_and_engine_on_pythonpath`; 3 `test_local_integrity_fail_blocks_the_push_and_reaches_the_prompt`; 4 `test_local_integrity_hold_still_pushes`; 5 `test_local_integrity_skipped_when_a_gate_command_failed`. `test_green_gate_pushes_and_opens_the_pr` was rewritten in place (same name) because the gate now makes a fourth command call. Mutation-checked: passing a failed entry, and passing the token through `--local`, each turn tests red. Gate: ruff, check_tests_first, pytest (838 passed) green.
