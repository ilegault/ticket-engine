"""Tests for ticket 69: single gate runner and engine-gate command.

WHY THIS EXISTS
---------------
ADR 0011 rules 3 and 4 establish that a repo has one gate list run across
the baseline, pre-push gate, and CI, and that every command must run even
after a failure. These tests verify the pure result collection, report
formatting, and engine-gate CLI entry with injected command runners.
"""
from __future__ import annotations

import pathlib

import pytest

from ticket_engine.gate import (
    GateCommandResult,
    GateResult,
    format_gate_report,
    main,
    run_gate,
)


def test_run_gate_runs_every_command_after_a_failure():
    """run_gate executes all commands in order even if one or more fail.

    Three commands scripted 1, 0, 1: asserts all three were called in order and
    passed is False.
    """
    calls: list[tuple[str | list[str], str, dict[str, str] | None, bool]] = []
    scripts = [(1, "first fail"), (0, "second ok"), (1, "third fail")]

    def fake_runner(args, cwd, env, shell):
        calls.append((args, cwd, env, shell))
        return scripts[len(calls) - 1]

    commands = ["cmd1", "cmd2", "cmd3"]
    result = run_gate(commands, cwd="/test/dir", env={"VAR": "1"}, run=fake_runner)

    assert [c[0] for c in calls] == ["cmd1", "cmd2", "cmd3"]
    assert all(c[1] == "/test/dir" for c in calls)
    assert all(c[2] == {"VAR": "1"} for c in calls)
    assert all(c[3] is True for c in calls)
    assert result.passed is False
    assert len(result.results) == 3
    assert result.results[0].passed is False
    assert result.results[1].passed is True
    assert result.results[2].passed is False


def test_run_gate_keeps_order_exit_codes_and_output_tails():
    """GateCommandResult records command, exit code, passed status, and output tail.

    One output of 10 000 characters: asserts its tail is exactly the last 4 000.
    """
    calls: list[str] = []
    long_output = "head" + ("x" * 5996) + ("y" * 4000)  # total 10 000 chars
    assert len(long_output) == 10000

    def fake_runner(args, cwd, env, shell):
        calls.append(str(args))
        if args == "cmd_long":
            return (0, long_output)
        return (42, "short error")

    result = run_gate(["cmd_long", "cmd_err"], cwd=".", env=None, run=fake_runner, tail_chars=4000)

    assert isinstance(result.results, tuple)
    assert len(result.results) == 2

    res0 = result.results[0]
    assert isinstance(res0, GateCommandResult)
    assert res0.command == "cmd_long"
    assert res0.exit_code == 0
    assert res0.passed is True
    assert len(res0.output_tail) == 4000
    assert res0.output_tail == "y" * 4000

    res1 = result.results[1]
    assert isinstance(res1, GateCommandResult)
    assert res1.command == "cmd_err"
    assert res1.exit_code == 42
    assert res1.passed is False
    assert res1.output_tail == "short error"

    assert result.passed is False


def test_gate_report_lists_each_command_and_failing_output():
    """format_gate_report has PASS/FAIL lines and failing output blocks."""
    res_pass = GateCommandResult(command="ruff check .", exit_code=0, passed=True, output_tail="")
    res_fail = GateCommandResult(
        command="pytest -q",
        exit_code=1,
        passed=False,
        output_tail="1 failed, 10 passed",
    )
    result = GateResult(passed=False, results=(res_pass, res_fail))

    expected = (
        "PASS ruff check .\n"
        "FAIL (exit 1) pytest -q\n"
        "--- pytest -q ---\n"
        "1 failed, 10 passed"
    )
    assert format_gate_report(result) == expected


def test_engine_gate_runs_the_repo_gate_commands_with_test_env(tmp_path: pathlib.Path):
    """engine-gate reads .ticket-engine.toml, runs gate_commands with test_env."""
    config_file = tmp_path / ".ticket-engine.toml"
    config_file.write_text(
        'gate_commands = ["check-one", "check-two"]\n\n'
        '[test_env]\n'
        'CUSTOM_KEY = "custom_val"\n',
        encoding="utf-8",
    )

    calls: list[tuple[str | list[str], str, dict[str, str] | None, bool]] = []

    def fake_runner(args, cwd, env, shell):
        calls.append((args, cwd, env, shell))
        return (0, "ok")

    exit_code = main(["--repo-path", str(tmp_path)], run=fake_runner)

    assert exit_code == 0
    assert len(calls) == 2
    assert calls[0][0] == "check-one"
    assert calls[0][1] == str(tmp_path)
    assert calls[0][2] == {"CUSTOM_KEY": "custom_val"}
    assert calls[0][3] is True

    assert calls[1][0] == "check-two"
    assert calls[1][1] == str(tmp_path)
    assert calls[1][2] == {"CUSTOM_KEY": "custom_val"}
    assert calls[1][3] is True


def test_engine_gate_exit_code_follows_the_gate(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]):
    """engine-gate returns 0 when all green and 1 when any command is red."""
    config_file = tmp_path / ".ticket-engine.toml"
    config_file.write_text(
        'gate_commands = ["cmd1"]\n',
        encoding="utf-8",
    )

    # 1. All green -> 0
    def green_runner(args, cwd, env, shell):
        return (0, "green output")

    exit_0 = main(["--repo-path", str(tmp_path)], run=green_runner)
    captured_0 = capsys.readouterr().out
    assert exit_0 == 0
    assert "PASS cmd1" in captured_0

    # 2. One red -> 1
    def red_runner(args, cwd, env, shell):
        return (1, "failing output")

    exit_1 = main(["--repo-path", str(tmp_path)], run=red_runner)
    captured_1 = capsys.readouterr().out
    assert exit_1 == 1
    assert "FAIL (exit 1) cmd1" in captured_1
    assert "--- cmd1 ---" in captured_1
    assert "failing output" in captured_1
