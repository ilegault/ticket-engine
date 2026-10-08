"""Single gate runner and the `engine-gate` console entry.

WHY THIS EXISTS
---------------
ADR 0011 (rules 3 and 4) and ADR 0010 (rule 4) require that each repository has
exactly one gate list (`gate_commands` in `.ticket-engine.toml`), and that the
baseline, the pre-push gate, and CI all run that exact list.

The gate runner always runs every command in the list, even after one fails, so
that a worker or developer sees every failure in a single run (e.g. a failing
linter does not hide a failing test or type gate).

The `engine-gate` console command is called by the shared `gate.yml` workflow
in CI and runs the current repo's gate list with its declared `test_env`.
"""
from __future__ import annotations

import argparse
import logging
import pathlib
from dataclasses import dataclass

from ticket_engine.config import load_repo_config
from ticket_engine.repo_env import CommandRunner, default_command_runner

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GateCommandResult:
    command: str
    exit_code: int
    passed: bool
    output_tail: str


@dataclass(frozen=True)
class GateResult:
    passed: bool
    results: tuple[GateCommandResult, ...] = ()


def run_gate(
    commands: list[str],
    cwd: str,
    env: dict[str, str] | None,
    run: CommandRunner = default_command_runner,
    tail_chars: int = 4000,
) -> GateResult:
    """Run every gate command in order, never stopping early on failure.

    Calls `run(command, cwd, env, True)` once per command in order.
    Returns a GateResult whose `passed` is True only if every command exited 0.
    """
    results: list[GateCommandResult] = []
    for command in commands:
        exit_code, output = run(command, cwd, env, True)
        output_str = output or ""
        output_tail = output_str[-tail_chars:] if len(output_str) > tail_chars else output_str
        results.append(
            GateCommandResult(
                command=command,
                exit_code=exit_code,
                passed=(exit_code == 0),
                output_tail=output_tail,
            )
        )

    all_passed = all(r.passed for r in results)
    return GateResult(passed=all_passed, results=tuple(results))


def format_gate_report(result: GateResult) -> str:
    """Format a human- and worker-readable report of the gate run.

    Has one line per command: PASS <command> or FAIL (exit <n>) <command>,
    followed by a block headed `--- <command> ---` with output_tail for each failed command.
    """
    lines: list[str] = []
    for r in result.results:
        if r.passed:
            lines.append(f"PASS {r.command}")
        else:
            lines.append(f"FAIL (exit {r.exit_code}) {r.command}")

    for r in result.results:
        if not r.passed:
            lines.append(f"--- {r.command} ---")
            tail = r.output_tail.rstrip("\r\n")
            if tail:
                lines.append(tail)

    return "\n".join(lines)


def main(
    argv: list[str] | None = None,
    run: CommandRunner = default_command_runner,
) -> int:
    """CLI entry point for engine-gate."""
    parser = argparse.ArgumentParser(description="Run the repository gate commands.")
    parser.add_argument(
        "--repo-path",
        default=".",
        help="Path to repository root (default: current directory).",
    )
    args = parser.parse_args(argv)

    repo_path = pathlib.Path(args.repo_path)
    cfg = load_repo_config(repo_path)

    result = run_gate(cfg.gate_commands, str(repo_path), cfg.test_env, run)
    report = format_gate_report(result)
    if report:
        print(report)

    return 1 if any(not r.passed for r in result.results) else 0
