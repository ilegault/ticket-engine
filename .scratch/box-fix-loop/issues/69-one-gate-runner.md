# 69: One gate runner, and `engine-gate` runs a repo's gate list

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0011 rules 3 and 4; ADR 0010 rule 4

## What to build

One function that runs a repo's gate list, used later by the baseline (ticket 62),
the pre-push gate (tickets 72–73) and CI (ticket 83). It always runs every command,
even after one fails, so a worker sees every failure at once. A console command,
`engine-gate`, runs the gate list of the repo it is started in; the shared `gate`
workflow will call it.

New module `src/ticket_engine/gate.py`. The command runner is injected, with the
same `CommandRunner` signature `repo_env.py` already uses
(`run(args, cwd, env, shell) -> (exit_code, output)`), so tests never start a process.

## Acceptance criteria

Write the tests first, in a new `tests/test_gate.py`, and watch each fail before
changing `src/`. Tests fake the command runner with a recording function that
returns scripted `(exit_code, output)` pairs; `load_repo_config` and the
`.ticket-engine.toml` it reads are real, written into `tmp_path`.

- [x] **Every command runs.** `run_gate(commands: list[str], cwd: str, env: dict[str, str] | None, run: CommandRunner = default_command_runner, tail_chars: int = 4000) -> GateResult` calls `run(command, cwd, env, True)` once per command, in order, and never stops early. `GateResult.passed` is `True` only when every exit code is 0. Test `test_run_gate_runs_every_command_after_a_failure`: three commands scripted `1, 0, 1`; asserts all three were called in order and `passed is False`.
- [x] **Results keep order, exit codes and output tails.** `GateResult.results` is a tuple of frozen `GateCommandResult(command: str, exit_code: int, passed: bool, output_tail: str)`, where `output_tail` is the last `tail_chars` characters of the output. Test `test_run_gate_keeps_order_exit_codes_and_output_tails` (one output of 10 000 characters, asserts its tail is exactly the last 4 000).
- [x] **A report a person and a worker can read.** `format_gate_report(result: GateResult) -> str` has one line per command, `PASS <command>` or `FAIL (exit <n>) <command>`, then for each failed command a block headed `--- <command> ---` holding its `output_tail`. Test `test_gate_report_lists_each_command_and_failing_output` asserts the exact text for one pass and one fail.
- [x] **`engine-gate` runs the repo's list with its test environment.** `main(argv: list[str] | None = None, run: CommandRunner = default_command_runner) -> int` takes `--repo-path` (default `.`), loads `load_repo_config(repo_path)`, calls `run_gate(cfg.gate_commands, str(repo_path), cfg.test_env, run)`, prints `format_gate_report`, and returns 1 if any command failed, else 0. Add `engine-gate = "ticket_engine.gate:main"` under `[project.scripts]` in `pyproject.toml`. Tests `test_engine_gate_runs_the_repo_gate_commands_with_test_env` (a real `.ticket-engine.toml` with two `gate_commands` and a `[test_env]` table; asserts the runner saw both commands and that env) and `test_engine_gate_exit_code_follows_the_gate` (all green → 0, one red → 1).
- [x] **Nothing else changes.** (no test: the full existing suite passes unchanged; no existing test is edited)

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments

2026-10-07: Implemented the single gate runner `run_gate`, report formatter `format_gate_report`, and `engine-gate` console entry point in `src/ticket_engine/gate.py`.
- Criterion 1 (Every command runs): covered by `test_run_gate_runs_every_command_after_a_failure`.
- Criterion 2 (Results keep order, exit codes, and output tails): covered by `test_run_gate_keeps_order_exit_codes_and_output_tails`.
- Criterion 3 (A report a person and a worker can read): covered by `test_gate_report_lists_each_command_and_failing_output`.
- Criterion 4 (`engine-gate` runs repo list with test environment): covered by `test_engine_gate_runs_the_repo_gate_commands_with_test_env` and `test_engine_gate_exit_code_follows_the_gate`. Added `engine-gate = "ticket_engine.gate:main"` to `[project.scripts]` in `pyproject.toml`.
- Criterion 5 (Nothing else changes): Full existing test suite passes unchanged; no existing test function is edited or deleted.
- Gates verified locally in CI order: `ruff check .` clean, `python scripts/check_tests_first.py` passed, `pytest -q` passed (784 passed, 1 skipped).
