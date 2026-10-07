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

import ticket_engine
from ticket_engine import box_worker
from ticket_engine.box_core import CloseAlert, RaiseAlert
from ticket_engine.box_status import AlertKind, BoxState, parse_box_status, render_box_alert
from ticket_engine.local_config import (
    BoxConfigError,
    LocalRepoEntry,
    LocalWorkerConfig,
    load_box_config,
)
from ticket_engine.parser import Ticket, TicketParser
from ticket_engine.repo_list import RepoListEntry
from ticket_engine.sonnet import SonnetDriver

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
        self.escalate_fixes_calls: list[tuple] = []
        self.claims: dict[int, str] = {}
        self.run_one_result: object = FakeRunResult("success")
        self.fix_ci_result: object = FakeRunResult("success")

    def run_one(self, entry, ticket, now=None, box_mode=False):
        self.run_one_calls.append((entry, ticket, box_mode))
        return self.run_one_result

    def fix_ci(self, entry, ticket, pr_number):
        self.fix_ci_calls.append((entry, ticket, pr_number))
        return self.fix_ci_result

    def escalate_fixes(self, entry, ticket, pr_number):
        self.escalate_fixes_calls.append((entry, ticket, pr_number))

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
    repo_list_fn: object | None = None,
    git_runner: object | None = None,
    **config_kwargs,
) -> box_worker.BoxLoop:
    config = make_config(tmp_path, **config_kwargs)
    clock = {"now": now}
    loop = box_worker.BoxLoop(
        config=config,
        worker=worker,
        github_client=github,
        git_runner=git_runner if git_runner is not None else (lambda args, cwd, env: (0, "")),
        sleep_fn=lambda s: None,
        now_fn=lambda: clock["now"],
        ticket_loader=lambda entry: list(tickets or []),
        repo_list_fn=repo_list_fn,
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
        f"logs_dir = '{tmp_path / 'logs'}'\n",
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


# ---------------------------------------------------------------------------
# Ticket 57: folder settings and strict box config loader
# ---------------------------------------------------------------------------

def test_local_config_reads_projects_and_envs_dirs(tmp_path):
    from ticket_engine.local_config import load_local_config

    default_cfg = LocalWorkerConfig()
    assert default_cfg.projects_dir == str(pathlib.Path.home() / "projects")
    assert default_cfg.envs_dir == str(pathlib.Path.home() / "envs")

    custom_file = tmp_path / "custom.toml"
    custom_file.write_text(
        'projects_dir = "/custom/projects"\n'
        'envs_dir = "/custom/envs"\n',
        encoding="utf-8",
    )
    custom_cfg = load_local_config(custom_file)
    assert custom_cfg.projects_dir == "/custom/projects"
    assert custom_cfg.envs_dir == "/custom/envs"

    empty_file = tmp_path / "empty.toml"
    empty_file.write_text(
        'projects_dir = ""\n'
        'envs_dir = ""\n',
        encoding="utf-8",
    )
    fallback_cfg = load_local_config(empty_file)
    assert fallback_cfg.projects_dir == str(pathlib.Path.home() / "projects")
    assert fallback_cfg.envs_dir == str(pathlib.Path.home() / "envs")


def test_load_box_config_raises_when_missing(tmp_path):
    missing_path = tmp_path / "nonexistent.toml"
    with pytest.raises(BoxConfigError) as exc_info:
        load_box_config(missing_path)
    msg = str(exc_info.value)
    assert str(missing_path) in msg
    assert "not found" in msg


def test_load_box_config_raises_when_unreadable(tmp_path):
    unreadable_path = tmp_path / "corrupt.toml"
    unreadable_path.write_text("invalid = [toml unclosed", encoding="utf-8")
    with pytest.raises(BoxConfigError) as exc_info:
        load_box_config(unreadable_path)
    msg = str(exc_info.value)
    assert str(unreadable_path) in msg
    assert "unreadable" in msg


def test_load_box_config_raises_when_has_repos(tmp_path):
    repos_path = tmp_path / "with_repos.toml"
    repos_path.write_text(
        '[[repos]]\npath = "/a"\nrepo = "b/c"\n',
        encoding="utf-8",
    )
    with pytest.raises(BoxConfigError) as exc_info:
        load_box_config(repos_path)
    msg = str(exc_info.value)
    assert "[[repos]]" in msg
    assert "engine-repos.toml" in msg


def test_load_box_config_accepts_a_config_without_repos(tmp_path):
    valid_path = tmp_path / "valid.toml"
    valid_path.write_text(
        'engine_repo = "owner/custom"\n',
        encoding="utf-8",
    )
    cfg = load_box_config(valid_path)
    assert isinstance(cfg, LocalWorkerConfig)
    assert cfg.engine_repo == "owner/custom"
    assert cfg.repos == []


def test_main_refuses_a_missing_config(tmp_path, monkeypatch, caplog):
    missing_path = tmp_path / "missing.toml"

    def fail_build_loop(config):
        pytest.fail("build_loop should not be called on bad config")

    monkeypatch.setattr(box_worker, "build_loop", fail_build_loop)
    monkeypatch.setattr(box_worker, "_configure_logging", lambda logs_dir: None)

    with caplog.at_level(logging.ERROR, logger="ticket_engine.box_worker"):
        rc = box_worker.main(["--config", str(missing_path)])

    assert rc == 2
    assert any(
        "box-worker refuses to start:" in r.getMessage() and r.levelno == logging.ERROR
        for r in caplog.records
    )


def test_main_refuses_a_config_with_repos(tmp_path, monkeypatch, caplog):
    repos_path = tmp_path / "repos.toml"
    repos_path.write_text(
        '[[repos]]\npath = "/a"\nrepo = "b/c"\n',
        encoding="utf-8",
    )

    def fail_build_loop(config):
        pytest.fail("build_loop should not be called on bad config")

    monkeypatch.setattr(box_worker, "build_loop", fail_build_loop)
    monkeypatch.setattr(box_worker, "_configure_logging", lambda logs_dir: None)

    with caplog.at_level(logging.ERROR, logger="ticket_engine.box_worker"):
        rc = box_worker.main(["--config", str(repos_path)])

    assert rc == 2
    assert any(
        "box-worker refuses to start:" in r.getMessage() and r.levelno == logging.ERROR
        for r in caplog.records
    )


def test_main_once_runs_exactly_one_tick(tmp_path, monkeypatch):
    config_path = tmp_path / "local.toml"
    config_path.write_text(
        f"engine_repo = 'owner/engine'\nlogs_dir = '{tmp_path / 'logs'}'\n",
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


# ---------------------------------------------------------------------------
# Ticket 41: build_loop wires the optional Sonnet fallback
# ---------------------------------------------------------------------------

def test_build_loop_sonnet_disabled_by_default():
    """build_loop(config) with sonnet_enabled=False leaves worker.sonnet_driver None."""
    config = LocalWorkerConfig(repos=[LocalRepoEntry(path="/fake", repo="owner/repo")])
    loop = box_worker.build_loop(config)
    assert loop.worker.sonnet_driver is None


def test_build_loop_sonnet_enabled_wiring():
    """build_loop(config) with sonnet_enabled=True passes a SonnetDriver with
    the configured timeout_seconds through to worker.sonnet_driver."""
    config = LocalWorkerConfig(
        repos=[LocalRepoEntry(path="/fake", repo="owner/repo")],
        sonnet_enabled=True,
        sonnet_timeout_seconds=3600,
    )
    loop = box_worker.build_loop(config)
    assert isinstance(loop.worker.sonnet_driver, SonnetDriver)
    assert loop.worker.sonnet_driver.timeout_seconds == 3600


# ---------------------------------------------------------------------------
# Box setup follow-ups (found during the first real box setup):
# - the API token comes from the local config, not only PIPELINE_TOKEN;
# - every tick leaves one log line, so a healthy box is distinguishable from
#   a stuck one;
# - a failed `git pull` is logged, never silently ignored;
# - git calls have a timeout, so a hidden credential prompt cannot hang the box;
# - a tick that raises is logged before it propagates (the scheduled task has
#   no console, so an unlogged traceback is lost).
# ---------------------------------------------------------------------------

def _box_records(caplog, level: int) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.name == "ticket_engine.box_worker" and r.levelno == level
    ]


def test_build_loop_uses_the_config_token_for_the_github_api(monkeypatch):
    monkeypatch.delenv("PIPELINE_TOKEN", raising=False)
    config = LocalWorkerConfig(
        repos=[LocalRepoEntry(path="/fake", repo="owner/repo")],
        github_token="tok-from-config",
    )
    loop = box_worker.build_loop(config)
    assert loop.github_client.token == "tok-from-config"
    assert loop.worker.config.github_token == "tok-from-config"


def test_build_loop_config_token_wins_over_the_env_var(monkeypatch):
    monkeypatch.setenv("PIPELINE_TOKEN", "tok-from-env")
    config = LocalWorkerConfig(
        repos=[LocalRepoEntry(path="/fake", repo="owner/repo")],
        github_token="tok-from-config",
    )
    loop = box_worker.build_loop(config)
    assert loop.github_client.token == "tok-from-config"


def test_build_loop_falls_back_to_pipeline_token_for_api_and_pushes(monkeypatch):
    monkeypatch.setenv("PIPELINE_TOKEN", "tok-from-env")
    config = LocalWorkerConfig(repos=[LocalRepoEntry(path="/fake", repo="owner/repo")])
    loop = box_worker.build_loop(config)
    assert loop.github_client.token == "tok-from-env"
    # The worker authenticates its pushes with config.github_token, so the
    # fallback must reach it too, not just the API client.
    assert loop.worker.config.github_token == "tok-from-env"


def test_build_loop_wires_env_for_to_the_repo_environment(tmp_path):
    envs_dir = str(tmp_path / "envs")
    config = LocalWorkerConfig(
        repos=[LocalRepoEntry(path="/fake", repo="owner/repo")],
        envs_dir=envs_dir,
    )
    loop = box_worker.build_loop(config)
    env = loop.worker._env_for(make_entry(repo="o/r"))
    assert env is not None
    assert env["VIRTUAL_ENV"] == str(pathlib.Path(envs_dir) / "r")
    assert env["VIRTUAL_ENV"].endswith("r")



def test_every_tick_logs_the_step_it_took(tmp_path, caplog):
    loop = make_loop(tmp_path, FakeWorker(), make_github(), tickets=[])

    with caplog.at_level(logging.INFO, logger="ticket_engine.box_worker"):
        loop.tick()  # first tick of a run always writes the status issue
        loop.tick()  # then nothing to do: wait one poll interval

    assert [r.getMessage() for r in _box_records(caplog, logging.INFO)] == [
        "tick: write status",
        "tick: wait until 2026-09-26T12:10Z",
    ]


def test_tick_log_names_the_ticket_it_claims(tmp_path, caplog):
    loop = make_loop(tmp_path, FakeWorker(), make_github(), tickets=[make_ticket(9)])
    loop._last_status_write = _NOW

    with caplog.at_level(logging.INFO, logger="ticket_engine.box_worker"):
        loop.tick()

    assert [r.getMessage() for r in _box_records(caplog, logging.INFO)] == [
        "tick: claim owner/repo #09",
    ]


def test_failed_pull_is_logged_and_the_tick_still_runs(tmp_path, caplog):
    github = make_github()
    loop = make_loop(tmp_path, FakeWorker(), github, tickets=[])
    loop._git_runner = lambda args, cwd, env: (
        128, "fatal: Not possible to fast-forward, aborting."
    )

    with caplog.at_level(logging.WARNING, logger="ticket_engine.box_worker"):
        loop.tick()

    warnings = [r.getMessage() for r in _box_records(caplog, logging.WARNING)]
    assert warnings == [
        (
            "git pull failed in /fake/repo (exit 128): "
            "fatal: Not possible to fast-forward, aborting."
        )
    ]
    github.update_issue_body.assert_called_once()


def test_successful_pull_logs_no_warning(tmp_path, caplog):
    loop = make_loop(tmp_path, FakeWorker(), make_github(), tickets=[])

    with caplog.at_level(logging.WARNING, logger="ticket_engine.box_worker"):
        loop.tick()

    assert _box_records(caplog, logging.WARNING) == []


def test_a_failing_tick_is_logged_then_raised(tmp_path, caplog):
    github = make_github()
    github.update_issue_body.side_effect = RuntimeError("boom")
    loop = make_loop(tmp_path, FakeWorker(), github, tickets=[])

    with (
        caplog.at_level(logging.ERROR, logger="ticket_engine.box_worker"),
        pytest.raises(RuntimeError, match="boom"),
    ):
        loop.tick()

    errors = _box_records(caplog, logging.ERROR)
    assert [r.getMessage() for r in errors] == ["tick failed"]
    assert errors[0].exc_info is not None


def test_local_config_git_timeout_default_and_toml(tmp_path):
    from ticket_engine.local_config import load_local_config

    assert LocalWorkerConfig().git_timeout_seconds == 300
    config_file = tmp_path / "local.toml"
    config_file.write_text("git_timeout_seconds = 45\n", encoding="utf-8")
    assert load_local_config(config_file).git_timeout_seconds == 45


def test_default_git_runner_gives_up_after_its_timeout():
    import sys
    import time

    started = time.monotonic()
    rc, out = box_worker._default_git_runner(
        [sys.executable, "-c", "import time; time.sleep(30)"], None, None, timeout=0.5
    )
    assert rc != 0
    assert "timed out after 0.5s" in out
    assert time.monotonic() - started < 10


def test_box_loop_pulls_with_the_configured_git_timeout(tmp_path, monkeypatch):
    seen: list[tuple[list[str], object]] = []

    def fake_runner(args, cwd=None, env=None, timeout=None):
        seen.append((args, timeout))
        return 0, ""

    monkeypatch.setattr(box_worker, "_default_git_runner", fake_runner)
    loop = box_worker.BoxLoop(
        config=make_config(tmp_path, git_timeout_seconds=42),
        worker=FakeWorker(),
        github_client=make_github(),
        sleep_fn=lambda s: None,
        now_fn=lambda: _NOW,
        ticket_loader=lambda entry: [],
    )

    loop.tick()

    engine_checkout = pathlib.Path(ticket_engine.__file__).resolve().parents[2]
    assert seen == [
        (["git", "-C", "/fake/repo", "pull", "--ff-only"], 42),
        (["git", "-C", str(engine_checkout), "rev-parse", "HEAD"], 42),
    ]


# ---------------------------------------------------------------------------
# Ticket 47: a claim or resume that does nothing must not spin the loop.
# ---------------------------------------------------------------------------

def test_a_resume_that_does_nothing_waits_a_poll_interval(tmp_path, caplog):
    """run_one returning False (refused or could not start) for a ResumeClaim
    makes the loop wait poll_interval_minutes, so the same no-op step is not
    retried in a tight loop against the GitHub API."""
    ticket = make_ticket(2, status="in-progress")
    worker = FakeWorker()
    worker.claims = {2: "box"}
    worker.run_one_result = False
    loop = make_loop(tmp_path, worker, make_github(), tickets=[ticket])
    loop._last_status_write = _NOW
    sleeps: list[float] = []
    loop._sleep = sleeps.append

    with caplog.at_level(logging.WARNING, logger="ticket_engine.box_worker"):
        loop.tick()

    assert len(worker.run_one_calls) == 1
    assert sleeps == [10 * 60]
    assert "did nothing" in caplog.text


def test_a_claim_that_does_nothing_waits_a_poll_interval(tmp_path):
    ticket = make_ticket(3)
    worker = FakeWorker()
    worker.run_one_result = False
    loop = make_loop(tmp_path, worker, make_github(), tickets=[ticket])
    loop._last_status_write = _NOW
    sleeps: list[float] = []
    loop._sleep = sleeps.append

    loop.tick()

    assert len(worker.run_one_calls) == 1
    assert sleeps == [10 * 60]


def test_a_claim_that_worked_does_not_add_a_wait(tmp_path):
    ticket = make_ticket(3)
    worker = FakeWorker()
    loop = make_loop(tmp_path, worker, make_github(), tickets=[ticket])
    loop._last_status_write = _NOW
    sleeps: list[float] = []
    loop._sleep = sleeps.append

    loop.tick()

    assert len(worker.run_one_calls) == 1
    assert sleeps == []


# ---------------------------------------------------------------------------
# Ticket 58: Box works listed repos, clones missing, honours BOX_PAUSED
# ---------------------------------------------------------------------------

def test_box_works_listed_repos_in_list_order(tmp_path: pathlib.Path) -> None:
    projects_dir = tmp_path / "projects"
    (projects_dir / "repo-a").mkdir(parents=True)
    (projects_dir / "repo-b").mkdir(parents=True)

    repo_list = [
        RepoListEntry(repo="org/repo-a", box=True),
        RepoListEntry(repo="org/repo-b", box=True),
    ]
    loop = make_loop(
        tmp_path,
        FakeWorker(),
        make_github(),
        repo_list_fn=lambda: repo_list,
        projects_dir=str(projects_dir),
    )
    world = loop._build_world()
    assert [r.repo for r in world.repos] == ["org/repo-a", "org/repo-b"]
    assert world.repos[0].repo == "org/repo-a"
    assert world.repos[1].repo == "org/repo-b"
    entry_a = loop._entry_for("org/repo-a")
    assert entry_a.repo == "org/repo-a"
    assert entry_a.path == str(projects_dir / "repo-a")


def test_box_skips_box_false_entries(tmp_path: pathlib.Path) -> None:
    projects_dir = tmp_path / "projects"
    (projects_dir / "repo-a").mkdir(parents=True)
    (projects_dir / "repo-b").mkdir(parents=True)

    repo_list = [
        RepoListEntry(repo="org/repo-a", box=True),
        RepoListEntry(repo="org/repo-b", box=False),
    ]
    loop = make_loop(
        tmp_path,
        FakeWorker(),
        make_github(),
        repo_list_fn=lambda: repo_list,
        projects_dir=str(projects_dir),
    )
    world = loop._build_world()
    assert [r.repo for r in world.repos] == ["org/repo-a"]


def test_unreadable_repo_list_reuses_the_last_one(tmp_path: pathlib.Path) -> None:
    projects_dir = tmp_path / "projects"
    (projects_dir / "repo-a").mkdir(parents=True)

    state = {"fail": False}

    def failing_or_success_repo_list():
        if state["fail"]:
            raise RuntimeError("API unavailable")
        return [RepoListEntry(repo="org/repo-a", box=True)]

    loop = make_loop(
        tmp_path,
        FakeWorker(),
        make_github(),
        repo_list_fn=failing_or_success_repo_list,
        projects_dir=str(projects_dir),
    )
    world1 = loop._build_world()
    assert [r.repo for r in world1.repos] == ["org/repo-a"]

    state["fail"] = True
    world2 = loop._build_world()
    assert [r.repo for r in world2.repos] == ["org/repo-a"]


def test_missing_clone_is_cloned_and_recorded(tmp_path: pathlib.Path) -> None:
    projects_dir = tmp_path / "projects"
    logs_dir = tmp_path / "logs"
    git_calls: list[list[str]] = []

    def recording_git_runner(args, cwd, env):
        git_calls.append(args)
        return (0, "")

    repo_list = [RepoListEntry(repo="org/new-repo", box=True)]
    loop = make_loop(
        tmp_path,
        FakeWorker(),
        make_github(),
        repo_list_fn=lambda: repo_list,
        git_runner=recording_git_runner,
        projects_dir=str(projects_dir),
        logs_dir=str(logs_dir),
    )
    world = loop._build_world()
    assert [r.repo for r in world.repos] == ["org/new-repo"]

    expected_clone_args = [
        "git",
        "clone",
        "https://github.com/org/new-repo.git",
        str(projects_dir / "new-repo"),
    ]
    assert any(call == expected_clone_args for call in git_calls)

    box_repos_file = logs_dir / "box_repos.json"
    assert box_repos_file.is_file()
    assert json.loads(box_repos_file.read_text(encoding="utf-8")) == ["org/new-repo"]


def test_failed_clone_leaves_the_repo_out_this_tick(tmp_path: pathlib.Path) -> None:
    projects_dir = tmp_path / "projects"
    logs_dir = tmp_path / "logs"

    def failing_git_runner(args, cwd, env):
        if "clone" in args:
            return (1, "fatal: repository not found")
        return (0, "")

    repo_list = [RepoListEntry(repo="org/failing-repo", box=True)]
    loop = make_loop(
        tmp_path,
        FakeWorker(),
        make_github(),
        repo_list_fn=lambda: repo_list,
        git_runner=failing_git_runner,
        projects_dir=str(projects_dir),
        logs_dir=str(logs_dir),
    )
    world = loop._build_world()
    assert [r.repo for r in world.repos] == []
    box_repos_file = logs_dir / "box_repos.json"
    if box_repos_file.is_file():
        assert "org/failing-repo" not in json.loads(box_repos_file.read_text(encoding="utf-8"))


def test_delisted_repo_is_built_not_accepting_new(tmp_path: pathlib.Path) -> None:
    projects_dir = tmp_path / "projects"
    logs_dir = tmp_path / "logs"
    (projects_dir / "delisted-repo").mkdir(parents=True)
    logs_dir.mkdir(parents=True)
    (logs_dir / "box_repos.json").write_text(json.dumps(["org/delisted-repo"]), encoding="utf-8")

    loop = make_loop(
        tmp_path,
        FakeWorker(),
        make_github(),
        repo_list_fn=list,
        projects_dir=str(projects_dir),
        logs_dir=str(logs_dir),
    )
    world = loop._build_world()
    assert len(world.repos) == 1
    assert world.repos[0].repo == "org/delisted-repo"
    assert world.repos[0].accepting_new is False


def test_box_paused_variable_sets_paused_by_developer_status(tmp_path: pathlib.Path) -> None:
    gh = make_github()
    gh.get_repo_variable.side_effect = lambda repo, name: "true" if name == "BOX_PAUSED" else None

    loop = make_loop(tmp_path, FakeWorker(), gh)
    loop.tick()

    gh.update_issue_body.assert_called_once()
    _, _, body = gh.update_issue_body.call_args[0]
    assert "State: paused_by_developer" in body


<<<<<<< HEAD
# ---------------------------------------------------------------------------
# Ticket 70: Durable fix attempts, surviving restart, quota not counted
# ---------------------------------------------------------------------------


def test_fix_attempt_count_survives_a_new_loop(tmp_path: pathlib.Path) -> None:
    ticket = make_ticket(3, status="in-progress")
    worker1 = FakeWorker()
    worker1.claims = {3: "box"}
    github = make_github()
    github.find_open_pr.return_value = 55
    github.list_check_runs.return_value = [("pytest", "failure")]

    loop1 = make_loop(tmp_path, worker1, github, tickets=[ticket])
    loop1._last_status_write = _NOW

    # Two ticks on first loop:
    loop1.tick()
    loop1.tick()
    assert len(worker1.fix_ci_calls) == 2

    # Second loop on the same logs_dir:
    worker2 = FakeWorker()
    worker2.claims = {3: "box"}
    loop2 = make_loop(tmp_path, worker2, github, tickets=[ticket])
    loop2._last_status_write = _NOW

    # Two more ticks:
    loop2.tick()
    loop2.tick()

    assert len(worker1.fix_ci_calls) + len(worker2.fix_ci_calls) == 3
    assert len(worker2.escalate_fixes_calls) == 1
    _entry, called_ticket, pr_number = worker2.escalate_fixes_calls[0]
    assert called_ticket.number == 3
    assert pr_number == 55


def test_quota_fix_run_does_not_count_as_an_attempt(tmp_path: pathlib.Path) -> None:
    ticket = make_ticket(3, status="in-progress")
    worker = FakeWorker()
    worker.claims = {3: "box"}
    github = make_github()
    github.find_open_pr.return_value = 55
    github.list_check_runs.return_value = [("pytest", "failure")]

    # First call returns quota, second returns success
    worker.fix_ci_result = FakeRunResult("quota")

    loop = make_loop(tmp_path, worker, github, tickets=[ticket])
    loop._last_status_write = _NOW

    loop.tick()
    assert len(worker.fix_ci_calls) == 1

    # Second call returns success
    worker.fix_ci_result = FakeRunResult("success")
    # Reset quota pause record so the next tick does not wait on quota
    loop._write_pause_record(None, None, False)
    loop.tick()
    assert len(worker.fix_ci_calls) == 2

    # Assert ledger attempts is 1 after two fix runs, not 2
    ledger_path = tmp_path / "logs" / "fix_attempts.json"
    assert ledger_path.is_file()
    data = json.loads(ledger_path.read_text(encoding="utf-8"))
    pr_record = data.get("owner/repo#55")
    assert pr_record is not None
    assert pr_record["attempts"] == 1
=======
def test_worker_records_its_commit_as_good_after_a_tick(tmp_path: pathlib.Path) -> None:
    worker = FakeWorker()
    gh = make_github()

    def git_runner(args, cwd, env):
        if "rev-parse" in args and "HEAD" in args:
            return (0, "abc1234\n")
        return (0, "")

    loop = make_loop(tmp_path, worker, gh, git_runner=git_runner)
    loop.tick()

    state_path = tmp_path / "logs" / "launcher_state.json"
    assert state_path.is_file()
    state_data = json.loads(state_path.read_text(encoding="utf-8"))
    assert state_data["good_commit"] == "abc1234"
>>>>>>> 6db98d7 (79: box-launcher starts the box and rolls back a bad update)

