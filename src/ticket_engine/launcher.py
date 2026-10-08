"""Box launcher: starts box-worker, restarts on exit, and rolls back broken updates.

WHY THIS EXISTS
---------------
Ticket 79, ADR 0012: The box runs an editable install of its engine checkout and
will update itself between tickets (ticket 80). If an engine update breaks the box
such that it fails to start, a standalone launcher must detect repeated start
failures within 15 minutes of the update, check out the last known good commit,
reinstall the engine, and record the rollback so an alert can be raised.

Crucially, this module imports ONLY from the standard library (sys.stdlib_module_names)
and NEVER imports ticket_engine or any engine dependencies. That way, a broken
engine update cannot prevent the launcher from running or rolling back.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import pathlib
import subprocess
import sys
import time
from collections.abc import Callable
from typing import Any


@dataclasses.dataclass(frozen=True)
class LauncherState:
    good_commit: str = ""
    current_commit: str = ""
    updated_at: datetime.datetime | None = None
    failures: tuple[datetime.datetime, ...] = ()
    bad_commit: str = ""


@dataclasses.dataclass(frozen=True)
class Restart:
    delay_seconds: int


@dataclasses.dataclass(frozen=True)
class Rollback:
    commit: str


Action = Restart | Rollback


def load_state(path: pathlib.Path) -> LauncherState:
    """Load LauncherState from launcher_state.json if present, or return default."""
    if not path.is_file():
        return LauncherState()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return LauncherState()
        good_commit = str(data.get("good_commit", ""))
        current_commit = str(data.get("current_commit", ""))
        bad_commit = str(data.get("bad_commit", ""))
        updated_at_raw = data.get("updated_at")
        updated_at = (
            datetime.datetime.fromisoformat(updated_at_raw)
            if updated_at_raw
            else None
        )
        failures_raw = data.get("failures", [])
        failures = tuple(
            datetime.datetime.fromisoformat(item)
            for item in failures_raw
            if item
        )
        return LauncherState(
            good_commit=good_commit,
            current_commit=current_commit,
            updated_at=updated_at,
            failures=failures,
            bad_commit=bad_commit,
        )
    except (OSError, json.JSONDecodeError, ValueError):
        return LauncherState()


def save_state(path: pathlib.Path, state: LauncherState) -> None:
    """Save LauncherState to launcher_state.json in ISO UTC."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "good_commit": state.good_commit,
        "current_commit": state.current_commit,
        "updated_at": state.updated_at.isoformat() if state.updated_at else None,
        "failures": [dt.isoformat() for dt in state.failures],
        "bad_commit": state.bad_commit,
    }
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def after_exit(
    state: LauncherState,
    exit_code: int,
    now: datetime.datetime,
) -> tuple[LauncherState, Action]:
    """Pure decision on worker exit.

    - exit code 75 (worker updated engine) -> Restart(0)
    - any other code -> failure recorded. If current_commit != good_commit,
      updated_at is set, and 3 failures fall within 15 minutes after updated_at:
      -> Rollback(good_commit)
      Otherwise -> Restart(60).
    """
    if exit_code == 75:
        return state, Restart(delay_seconds=0)

    new_failures = (*state.failures, now)
    new_state = dataclasses.replace(state, failures=new_failures)

    if (
        state.current_commit != state.good_commit
        and state.updated_at is not None
        and state.good_commit != ""
    ):
        window_start = state.updated_at
        window_end = state.updated_at + datetime.timedelta(minutes=15)
        matching: list[datetime.datetime] = []
        for f in new_failures:
            f_norm = f
            ws_norm = window_start
            we_norm = window_end
            if f.tzinfo is None and window_start.tzinfo is not None:
                f_norm = f.replace(tzinfo=datetime.UTC)
            elif f.tzinfo is not None and window_start.tzinfo is None:
                ws_norm = window_start.replace(tzinfo=datetime.UTC)
                we_norm = window_end.replace(tzinfo=datetime.UTC)
            if ws_norm <= f_norm <= we_norm:
                matching.append(f)

        if len(matching) >= 3:
            return new_state, Rollback(commit=state.good_commit)

    return new_state, Restart(delay_seconds=60)


def main(
    argv: list[str] | None = None,
    run: Any = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime.datetime] | datetime.datetime | None = None,
) -> None:
    """Run the box launcher loop."""
    parser = argparse.ArgumentParser(prog="box-launcher")
    parser.add_argument("--engine-dir", required=True, type=pathlib.Path)
    parser.add_argument("--state-dir", required=True, type=pathlib.Path)
    args = parser.parse_args(argv)

    engine_dir = args.engine_dir
    state_dir = args.state_dir
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / "launcher_state.json"
    rollback_file = state_dir / "rollback.json"

    def get_now() -> datetime.datetime:
        if now is None:
            return datetime.datetime.now(datetime.UTC)
        if callable(now):
            return now()
        return now

    def do_run(cmd: list[str], cwd: pathlib.Path | str | None = None) -> int:
        if run is None:
            proc = subprocess.run(cmd, cwd=cwd, check=False)
            return proc.returncode
        try:
            res = run(cmd, cwd=cwd)
        except TypeError:
            res = run(cmd)
        if hasattr(res, "returncode"):
            return res.returncode
        if res is None:
            return 0
        return int(res)

    while True:
        state = load_state(state_file)
        exit_code = do_run(
            [sys.executable, "-m", "ticket_engine.box_worker"],
            cwd=str(engine_dir),
        )
        current_time = get_now()
        new_state, action = after_exit(state, exit_code, current_time)

        if isinstance(action, Rollback):
            do_run(
                ["git", "-C", str(engine_dir), "checkout", "--detach", action.commit],
                cwd=str(engine_dir),
            )
            do_run(
                [sys.executable, "-m", "pip", "install", "-e", str(engine_dir)],
                cwd=str(engine_dir),
            )
            rolled_back_state = LauncherState(
                good_commit=state.good_commit,
                current_commit=action.commit,
                updated_at=state.updated_at,
                failures=(),
                bad_commit=state.current_commit,
            )
            save_state(state_file, rolled_back_state)
            rollback_record = {
                "bad_commit": state.current_commit,
                "good_commit": action.commit,
                "at": current_time.isoformat(),
            }
            rollback_file.write_text(
                json.dumps(rollback_record, indent=2),
                encoding="utf-8",
            )
        elif isinstance(action, Restart):
            save_state(state_file, new_state)
            if action.delay_seconds > 0:
                sleep(action.delay_seconds)


if __name__ == "__main__":
    main()
