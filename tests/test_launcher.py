"""Tests for ticket_engine.launcher (ticket 79).

WHY THIS EXISTS
---------------
Ticket 79: box-launcher starts the box worker and rolls back an engine update
that breaks it. ADR 0012: The box updates its own engine code between tickets,
and rolls back a bad update.
"""
from __future__ import annotations

import ast
import datetime
import json
import pathlib
import sys

import pytest

from ticket_engine.launcher import (
    LauncherState,
    Restart,
    Rollback,
    after_exit,
    main,
    save_state,
)


def test_three_failed_starts_within_15_minutes_of_an_update_roll_back():
    t_update = datetime.datetime(2026, 10, 7, 10, 0, tzinfo=datetime.UTC)
    t1 = datetime.datetime(2026, 10, 7, 10, 2, tzinfo=datetime.UTC)
    t2 = datetime.datetime(2026, 10, 7, 10, 5, tzinfo=datetime.UTC)
    t3 = datetime.datetime(2026, 10, 7, 10, 9, tzinfo=datetime.UTC)

    state = LauncherState(
        good_commit="good123",
        current_commit="bad456",
        updated_at=t_update,
        failures=(),
        bad_commit="",
    )
    state, action1 = after_exit(state, exit_code=1, now=t1)
    assert action1 == Restart(delay_seconds=60)
    assert state.failures == (t1,)

    state, action2 = after_exit(state, exit_code=1, now=t2)
    assert action2 == Restart(delay_seconds=60)
    assert state.failures == (t1, t2)

    state, action3 = after_exit(state, exit_code=1, now=t3)
    assert action3 == Rollback(commit="good123")
    assert state.failures == (t1, t2, t3)


def test_failures_outside_the_window_just_restart():
    t_update = datetime.datetime(2026, 10, 7, 10, 0, tzinfo=datetime.UTC)
    t1 = datetime.datetime(2026, 10, 7, 10, 2, tzinfo=datetime.UTC)
    t2 = datetime.datetime(2026, 10, 7, 10, 5, tzinfo=datetime.UTC)
    t3 = datetime.datetime(2026, 10, 7, 10, 20, tzinfo=datetime.UTC)

    state = LauncherState(
        good_commit="good123",
        current_commit="bad456",
        updated_at=t_update,
        failures=(),
        bad_commit="",
    )
    state, _ = after_exit(state, exit_code=1, now=t1)
    state, _ = after_exit(state, exit_code=1, now=t2)
    state, action3 = after_exit(state, exit_code=1, now=t3)
    assert action3 == Restart(delay_seconds=60)


def test_update_exit_code_restarts_at_once():
    now = datetime.datetime(2026, 10, 7, 10, 0, tzinfo=datetime.UTC)
    state = LauncherState(
        good_commit="good123",
        current_commit="good123",
        updated_at=None,
        failures=(),
        bad_commit="",
    )
    state, action = after_exit(state, exit_code=75, now=now)
    assert action == Restart(delay_seconds=0)
    assert state.failures == ()


def test_no_rollback_when_running_the_good_commit():
    t_update = datetime.datetime(2026, 10, 7, 10, 0, tzinfo=datetime.UTC)
    t1 = datetime.datetime(2026, 10, 7, 10, 2, tzinfo=datetime.UTC)
    t2 = datetime.datetime(2026, 10, 7, 10, 5, tzinfo=datetime.UTC)
    t3 = datetime.datetime(2026, 10, 7, 10, 9, tzinfo=datetime.UTC)

    state = LauncherState(
        good_commit="good123",
        current_commit="good123",
        updated_at=t_update,
        failures=(),
        bad_commit="",
    )
    state, _ = after_exit(state, exit_code=1, now=t1)
    state, _ = after_exit(state, exit_code=1, now=t2)
    state, action3 = after_exit(state, exit_code=1, now=t3)
    assert action3 == Restart(delay_seconds=60)


def test_rollback_checks_out_the_good_commit_reinstalls_and_writes_the_record(tmp_path):
    engine_dir = tmp_path / "engine"
    engine_dir.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    t_update = datetime.datetime(2026, 10, 7, 10, 0, tzinfo=datetime.UTC)
    t1 = datetime.datetime(2026, 10, 7, 10, 2, tzinfo=datetime.UTC)
    t2 = datetime.datetime(2026, 10, 7, 10, 5, tzinfo=datetime.UTC)
    t3 = datetime.datetime(2026, 10, 7, 10, 9, tzinfo=datetime.UTC)

    initial_state = LauncherState(
        good_commit="good123",
        current_commit="bad456",
        updated_at=t_update,
        failures=(t1, t2),
        bad_commit="",
    )
    save_state(state_dir / "launcher_state.json", initial_state)

    clock_times = [t3]
    recorded_commands: list[list[str]] = []
    recorded_sleeps: list[float] = []

    class StopLoop(Exception):
        pass

    def fake_run(cmd, cwd=None):
        recorded_commands.append(cmd)
        if cmd == [sys.executable, "-m", "ticket_engine.box_worker"]:
            if len(recorded_commands) == 1:
                return 1
            raise StopLoop("loop stopped after rollback and re-start")
        return 0

    def fake_sleep(duration):
        recorded_sleeps.append(duration)

    with pytest.raises(StopLoop):
        main(
            argv=["--engine-dir", str(engine_dir), "--state-dir", str(state_dir)],
            run=fake_run,
            sleep=fake_sleep,
            now=lambda: clock_times.pop(0) if clock_times else t3,
        )

    expected_checkout = ["git", "-C", str(engine_dir), "checkout", "--detach", "good123"]
    expected_pip = [sys.executable, "-m", "pip", "install", "-e", str(engine_dir)]

    assert expected_checkout in recorded_commands
    assert expected_pip in recorded_commands

    checkout_idx = recorded_commands.index(expected_checkout)
    pip_idx = recorded_commands.index(expected_pip)
    assert checkout_idx < pip_idx

    rollback_file = state_dir / "rollback.json"
    assert rollback_file.is_file()
    rollback_data = json.loads(rollback_file.read_text(encoding="utf-8"))
    assert rollback_data == {
        "bad_commit": "bad456",
        "good_commit": "good123",
        "at": t3.isoformat(),
    }

    state_file = state_dir / "launcher_state.json"
    assert state_file.is_file()
    state_data = json.loads(state_file.read_text(encoding="utf-8"))
    assert state_data["good_commit"] == "good123"
    assert state_data["current_commit"] == "good123"
    assert state_data["bad_commit"] == "bad456"
    assert state_data["failures"] == []


def test_launcher_imports_only_the_standard_library():
    launcher_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / "src"
        / "ticket_engine"
        / "launcher.py"
    )
    source = launcher_path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(launcher_path))
    stdlib_modules = set(sys.stdlib_module_names)

    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root_pkg = alias.name.split(".")[0]
                imported_modules.add(root_pkg)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                root_pkg = node.module.split(".")[0]
                imported_modules.add(root_pkg)
            elif node.level > 0:
                imported_modules.add("ticket_engine")

    assert "ticket_engine" not in imported_modules
    assert imported_modules <= stdlib_modules
