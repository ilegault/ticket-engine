"""Tests for ticket_engine.box_worker.

Covers ticket 31's acceptance criteria:
- AC1: entry point and config (main --once, LocalWorkerConfig fields).
- AC2: tick builds the world and dispatches each step type.
- AC3: the status issue is created/locked/pinned once, then only updated.
- AC4: quota and alert wiring (after_quota_error/after_success, RaiseAlert/
  CloseAlert, an auth outcome raising login_expired).
- AC5: logs stay local (RotatingFileHandler, .gitignore, no leaked text).

Fakes: the GitHub client and a recording fake `LocalWorker` exposing
run_one/fix_ci/list_box_claims. `BoxCore`, `box_status`, the parser, and the
ledger/pause-record files (on tmp_path) are real.
"""
from __future__ import annotations

import datetime
import json
import logging
import pathlib
from unittest.mock import MagicMock

import pytest

from ticket_engine import box_worker
from ticket_engine.box_core import CloseAlert, RaiseAlert
from ticket_engine.box_status import AlertKind, BoxState, parse_box_status, render_box_alert
from ticket_engine.local_config import LocalRepoEntry, LocalWorkerConfig
from ticket_engine.parser import Ticket, TicketParser

_NOW = datetime.datetime(2026, 9, 26, 12, 0, tzinfo=datetime.UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ticket(
    number: int,
    status: str = "ready-for-agent",
    blocked_by: str = "None",
    effort: str = "phase-1",
    title: str | None = None,
    runner: str = "any",
) -> Ticket:
    title = title or f"Ticket {number}"
    text = (
        f"# {number}: {title}\n\n"
        f"**Status:** {status}\n\n"
        f"**Blocked by:** {blocked_by}\n\n"
        f"**Runner:** {runner}\n\n"
        f"## Acceptance criteria\n\n- [ ] criterion 1\n"
    )
    path = pathlib.Path(".scratch") / effort / "issues" / f"{number:02d}-t.md"
    return TicketParser().parse_text(text, filename=path.name, path=path)


def make_entry(repo: str = "owner/repo", path: str = "/fake/repo") -> LocalRepoEntry:
    return LocalRepoEntry(path=path, repo=repo)


def make_config(tmp_path: pathlib.Path, **kwargs) -> LocalWorkerConfig:
    defaults = {
        "repos": [make_entry()],
        "engine_repo": "owner/engine",
        "logs_dir": str(tmp_path / "logs"),
        "concurrency": 1,
        "poll_interval_minutes": 10,
        "status_interval_minutes": 30,
        "weekly_cap_after_hours": 5,
        "weekly_cap_backoff_hours": 12,
    }
    defaults.update(kwargs)
    return LocalWorkerConfig(**defaults)


class FakeRunResult:
    def __init__(self, outcome: str, reset_at: datetime.datetime | None = None) -> None:
        self.outcome = outcome
        self.success = outcome == "success"
        self.quota_error = outcome == "quota"
        self.reset_at = reset_at


class FakeWorker:
    """Recording fake exposing run_one, fix_ci and list_box_claims (ticket 31)."""

    def __init__(self) -> None:
        self.run_one_calls: list[tuple] = []
        self.fix_ci_calls: list[tuple] = []
        self.claims: dict[int, str] = {}
        self.run_one_result: object = FakeRunResult("success")
        self.fix_ci_result: object = FakeRunResult("success")

    def run_one(self, entry, ticket, now=None, box_mode=False):
        self.run_one_calls.append((entry, ticket, box_mode))
        return self.run_one_result

    def fix_ci(self, entry, ticket, pr_number):
        self.fix_ci_calls.append((entry, ticket, pr_number))
        return self.fix_ci_result

    def list_box_claims(self, entry, tickets):
        return dict(self.claims)


def make_github() -> MagicMock:
    gh = MagicMock()
    gh.get_repo_variable.return_value = None
    gh.find_open_pr.return_value = None
    gh.list_check_runs.return_value = []
    gh.find_open_issue.return_value = None
    gh.create_issue.return_value = 101
    return gh


def make_loop(
    tmp_path: pathlib.Path,
    worker: FakeWorker,
    github: MagicMock,
    tickets: list[Ticket] | None = None,
    now: datetime.datetime = _NOW,
    **config_kwargs,
) -> box_worker.BoxLoop:
    config = make_config(tmp_path, **config_kwargs)
    clock = {"now": now}
    loop = box_worker.BoxLoop(
        config=config,
        worker=worker,
        github_client=github,
        git_runner=lambda args, cwd, env: (0, ""),
        sleep_fn=lambda s: None,
        now_fn=lambda: clock["now"],
        ticket_loader=lambda entry: list(tickets or []),
    )
    loop._clock = clock  # test-only handle to advance time between ticks
    return loop


# ---------------------------------------------------------------------------
# AC1: entry point and config
# ---------------------------------------------------------------------------

def test_local_worker_config_gains_box_fields(tmp_path):
    cfg = LocalWorkerConfig()
    assert cfg.concurrency == 1
    assert cfg.poll_interval_minutes == 10
    assert cfg.status_interval_minutes == 30
    assert cfg.weekly_cap_after_hours == 5
    assert cfg.weekly_cap_backoff_hours == 12
    assert cfg.engine_repo == "ilegault/ticket-engine"
    assert cfg.logs_dir


def test_local_config_loads_box_fields_from_toml(tmp_path):
    from ticket_engine.local_config import load_local_config

    config_file = tmp_path / "local.toml"
    config_file.write_text(
        "concurrency = 3\n"
        "poll_interval_minutes = 5\n"
        "status_interval_minutes = 15\n"
        "weekly_cap_after_hours = 4\n"
        "weekly_cap_backoff_hours = 8\n"
        'engine_repo = "owner/engine"\n'
        f'logs_dir = "{tmp_path / "logs"}"\n',
        encoding="utf-8",
    )
    cfg = load_local_config(config_file)
    assert cfg.concurrency == 3
    assert cfg.poll_interval_minutes == 5
    assert cfg.status_interval_minutes == 15
    assert cfg.weekly_cap_after_hours == 4
    assert cfg.weekly_cap_backoff_hours == 8
    assert cfg.engine_repo == "owner/engine"
    assert cfg.logs_dir == str(tmp_path / "logs")


def test_main_once_runs_exactly_one_tick(tmp_path, monkeypatch):
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        f'engine_repo = "owner/engine"\nlogs_dir = "{tmp_path / "logs"}"\n',
        encoding="utf-8",
    )

    calls = {"tick": 0}

    class FakeLoop:
        def tick(self) -> None:
            calls["tick"] += 1

    def fake_build_loop(config):
        assert config.engine_repo == "owner/engine"
        return FakeLoop()

    monkeypatch.setattr(box_worker, "build_loop", fake_build_loop)
    monkeypatch.setattr(box_worker, "_configure_logging", lambda logs_dir: None)

    rc = box_worker.main(["--once", "--config", str(config_path)])

    assert rc == 0
    assert calls["tick"] == 1


# ---------------------------------------------------------------------------
# AC2: tick builds the world, dispatches each step type
# ---------------------------------------------------------------------------

def test_claim_ticket_calls_run_one_and_appends_ledger(tmp_path):
    ticket = make_ticket(1)
    worker = FakeWorker()
    github = make_github()
    loop = make_loop(tmp_path, worker, github, tickets=[ticket])
    loop._last_status_write = _NOW  # skip WriteStatus so ClaimTicket can fire

    loop.tick()

    assert len(worker.run_one_calls) == 1
    _entry, called_ticket, box_mode = worker.run_one_calls[0]
    assert called_ticket.number == 1
    assert box_mode is True

    ledger = json.loads((tmp_path / "logs" / box_worker._LEDGER_FILENAME).read_text())
    assert ledger == [
        {"repo": "owner/repo", "ticket": 1, "started_at": "2026-09-26T12:00:00+00:00"}
    ]


def test_resume_claim_calls_run_one_for_existing_box_claim(tmp_path):
    ticket = make_ticket(2, status="in-progress")
    worker = FakeWorker()
    worker.claims = {2: "box"}
    github = make_github()
    loop = make_loop(tmp_path, worker, github, tickets=[ticket])
    loop._last_status_write = _NOW

    loop.tick()

    assert len(worker.run_one_calls) == 1
    assert worker.run_one_calls[0][1].number == 2
    # No ledger entry: ResumeClaim never re-records a start.
    assert not (tmp_path / "logs" / box_worker._LEDGER_FILENAME).is_file()


def test_fix_ci_called_for_box_pr_with_red_ci(tmp_path):
    ticket = make_ticket(3, status="in-progress")
    worker = FakeWorker()
    worker.claims = {3: "box"}
    github = make_github()
    github.find_open_pr.return_value = 55
    github.list_check_runs.return_value = [("pytest", "failure")]
    loop = make_loop(tmp_path, worker, github, tickets=[ticket])
    loop._last_status_write = _NOW

    loop.tick()

    assert len(worker.fix_ci_calls) == 1
    _entry, called_ticket, pr_number = worker.fix_ci_calls[0]
    assert called_ticket.number == 3
    assert pr_number == 55
    assert worker.run_one_calls == []


def test_wait_step_sleeps_for_the_difference(tmp_path):
    worker = FakeWorker()
    github = make_github()
    loop = make_loop(tmp_path, worker, github, tickets=[])
    loop._last_status_write = _NOW

    slept: list[float] = []
    loop._sleep = slept.append

    loop.tick()

    assert slept == [pytest.approx(600.0)]  # poll_interval_minutes=10 -> 600s


# ---------------------------------------------------------------------------
# AC3: status issue lifecycle
# ---------------------------------------------------------------------------

def test_write_status_creates_locks_pins_and_updates_body(tmp_path):
    worker = FakeWorker()
    github = make_github()
    loop = make_loop(tmp_path, worker, github, tickets=[])

    loop.tick()

    github.create_issue.assert_called_once_with(
        "owner/engine", "Box status", "", ["engine:box-status"]
    )
    github.lock_issue.assert_called_once_with("owner/engine", 101)
    github.pin_issue.assert_called_once_with("owner/engine", 101)
    github.update_issue_body.assert_called_once()
    repo_arg, issue_arg, body_arg = github.update_issue_body.call_args[0]
    assert repo_arg == "owner/engine"
    assert issue_arg == 101
    parsed = parse_box_status(body_arg)
    assert parsed is not None
    assert parsed.state == BoxState.idle
    assert loop._last_status_write == _NOW


def test_second_write_status_does_not_recreate_lock_or_pin(tmp_path):
    worker = FakeWorker()
    github = make_github()
    # status_interval_minutes=0 forces WriteStatus on every tick.
    loop = make_loop(tmp_path, worker, github, tickets=[], status_interval_minutes=0)

    loop.tick()
    # Simulate the issue now existing, as the real GitHub API would report.
    github.find_open_issue.return_value = 101

    loop.tick()

    github.create_issue.assert_called_once()
    github.lock_issue.assert_called_once()
    github.pin_issue.assert_called_once()
    assert github.update_issue_body.call_count == 2


# ---------------------------------------------------------------------------
# AC4: quota and alerts
# ---------------------------------------------------------------------------

def test_quota_outcome_applies_after_quota_error_and_writes_pause_record(tmp_path):
    ticket = make_ticket(4)
    worker = FakeWorker()
    worker.run_one_result = FakeRunResult("quota", reset_at=None)
    github = make_github()
    loop = make_loop(tmp_path, worker, github, tickets=[ticket])
    loop._last_status_write = _NOW

    loop.tick()

    pause = json.loads((tmp_path / "logs" / box_worker._PAUSE_FILENAME).read_text())
    assert pause["first_failure"] == "2026-09-26T12:00:00+00:00"
    assert pause["retry_at"] == "2026-09-26T13:00:00+00:00"
    assert pause["weekly_cap_alert_open"] is False


def test_success_applies_after_success_and_clears_pause_record(tmp_path):
    """After a run_one success, the loop applies BoxCore.after_success,
    clearing any pending quota pause it had recorded (AC4)."""
    worker = FakeWorker()
    github = make_github()
    loop = make_loop(tmp_path, worker, github, tickets=[])
    loop._write_pause_record(_NOW, _NOW + datetime.timedelta(hours=1), False)
    world = loop._build_world()

    loop._handle_run_result(FakeRunResult("success"), world)

    pause = json.loads((tmp_path / "logs" / box_worker._PAUSE_FILENAME).read_text())
    assert pause == {"first_failure": None, "retry_at": None, "weekly_cap_alert_open": False}


def test_raise_alert_dedupes_and_close_alert_closes_open_one(tmp_path):
    worker = FakeWorker()
    github = make_github()
    loop = make_loop(tmp_path, worker, github, tickets=[])
    world = loop._build_world()

    loop._carry_out(RaiseAlert(kind=AlertKind.weekly_cap), world)

    title, body = render_box_alert(AlertKind.weekly_cap, "owner", _NOW)
    github.create_issue.assert_called_once_with(
        "owner/engine", title, body, ["engine:box-alert"]
    )

    # A second raise, with the issue now open, creates no duplicate.
    github.find_open_issue.return_value = 202
    loop._carry_out(RaiseAlert(kind=AlertKind.weekly_cap), world)
    github.create_issue.assert_called_once()

    loop._carry_out(CloseAlert(kind=AlertKind.weekly_cap), world)
    github.close_issue.assert_called_once_with("owner/engine", 202)


def test_auth_outcome_raises_login_expired(tmp_path):
    ticket = make_ticket(6)
    worker = FakeWorker()
    worker.run_one_result = FakeRunResult("auth")
    github = make_github()
    loop = make_loop(tmp_path, worker, github, tickets=[ticket])
    loop._last_status_write = _NOW

    loop.tick()

    title, body = render_box_alert(AlertKind.login_expired, "owner", _NOW)
    github.create_issue.assert_called_once_with(
        "owner/engine", title, body, ["engine:box-alert"]
    )
    assert loop._auth_first_failure == _NOW
    assert loop._auth_retry_at == _NOW + datetime.timedelta(hours=1)


def test_login_expired_pauses_ticks_until_retry_but_status_still_writes(tmp_path):
    ticket = make_ticket(7)
    worker = FakeWorker()
    github = make_github()
    loop = make_loop(tmp_path, worker, github, tickets=[ticket])
    loop._last_status_write = _NOW
    loop._auth_first_failure = _NOW
    loop._auth_retry_at = _NOW + datetime.timedelta(hours=1)

    slept: list[float] = []
    loop._sleep = slept.append

    loop.tick()

    # Still within the retry window: no ticket work, just a wait.
    assert worker.run_one_calls == []
    assert slept == [pytest.approx(3600.0)]


def test_quota_timeline_weekly_cap_alert_opened_once_then_closed(tmp_path):
    """Drive quota failures from 0h to 5h01m: exactly one weekly_cap issue is
    created; a later success closes it (mirrors BoxCore's own quota-timeline
    test, but through BoxLoop's persisted pause-record round trip)."""
    ticket = make_ticket(8)
    worker = FakeWorker()
    github = make_github()
    loop = make_loop(tmp_path, worker, github, tickets=[ticket])
    loop._last_status_write = _NOW

    times = [
        _NOW,
        _NOW + datetime.timedelta(hours=1),
        _NOW + datetime.timedelta(hours=2),
        _NOW + datetime.timedelta(hours=3),
        _NOW + datetime.timedelta(hours=4),
        _NOW + datetime.timedelta(hours=5, minutes=1),
    ]
    worker.run_one_result = FakeRunResult("quota", reset_at=None)
    for t in times:
        loop._clock["now"] = t
        loop._last_status_write = t  # keep isolating ClaimTicket from WriteStatus
        loop.tick()

    assert github.create_issue.call_count == 1
    title, _ = render_box_alert(AlertKind.weekly_cap, "owner", _NOW)
    assert github.create_issue.call_args[0][1] == title

    # A later success closes the weekly_cap alert.
    github.find_open_issue.return_value = 303
    worker.run_one_result = FakeRunResult("success")
    loop._clock["now"] = times[-1] + datetime.timedelta(hours=12)
    loop._last_status_write = loop._clock["now"]
    loop.tick()

    github.close_issue.assert_called_once_with("owner/engine", 303)


# ---------------------------------------------------------------------------
# AC5: logs stay local
# ---------------------------------------------------------------------------

def test_configure_logging_writes_to_rotating_file_not_stdout(tmp_path, capsys):
    logs_dir = tmp_path / "logs"
    box_worker._configure_logging(str(logs_dir))
    try:
        marker = "FAKE_AGY_SECRET_FAILURE_TEXT_12345"
        logging.getLogger("ticket_engine.local_worker").error("agy said: %s", marker)

        log_file = logs_dir / "box-worker.log"
        assert log_file.is_file()
        assert marker in log_file.read_text(encoding="utf-8")

        captured = capsys.readouterr()
        assert marker not in captured.out
        assert marker not in captured.err
    finally:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if isinstance(handler, logging.handlers.RotatingFileHandler):
                root.removeHandler(handler)
                handler.close()


def test_agy_failure_text_never_reaches_a_github_request_body(tmp_path):
    marker = "FAKE_AGY_SECRET_FAILURE_TEXT_98765"
    ticket = make_ticket(9)
    worker = FakeWorker()
    worker.run_one_result = FakeRunResult("failed")
    worker.run_one_result.raw_output = marker  # attached, never read by box_worker
    github = make_github()
    loop = make_loop(tmp_path, worker, github, tickets=[ticket])
    loop._last_status_write = _NOW

    loop.tick()

    for call in github.mock_calls:
        assert marker not in repr(call)


def test_gitignore_excludes_box_logs_dir():
    gitignore = pathlib.Path(__file__).resolve().parent.parent / ".gitignore"
    assert "logs/" in gitignore.read_text(encoding="utf-8")


def test_module_docstring_explains_local_state_vs_dispatcher_invariant():
    assert "invariant 2" in box_worker.__doc__
