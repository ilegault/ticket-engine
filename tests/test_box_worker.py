"""Tests for ticket_engine.box_worker: the box loop's live-world adapter.

WHY THIS EXISTS
---------------
Ticket 31 (spec §The box loop, §Box status issue and alerts). `BoxCore`,
`box_status`, the parser and the ledger/pause-record files (on `tmp_path`) are
real; the GitHub client, `LocalWorker` and the clock/sleep are faked, exactly
as `AGENTS.md` §6 asks: a good test drives the adapter from the outside and
asserts what it would do in the world (which ticket it runs, which issue body
it writes, which alert it raises or closes), never its internal call order.
"""
from __future__ import annotations

import datetime
import json
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ticket_engine.box_status import AlertKind, BoxState, parse_box_status
from ticket_engine.box_worker import BoxLoop, main
from ticket_engine.local_config import LocalRepoEntry, LocalWorkerConfig
from ticket_engine.parser import Ticket, TicketParser

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ticket(
    number: int,
    status: str,
    effort: str = "box-primary-worker",
    runner: str = "any",
    title: str | None = None,
) -> Ticket:
    """Parse a real ticket file body, the same way the box reads a target repo."""
    title = title or f"Ticket {number}"
    text = (
        f"# {number}: {title}\n\n"
        f"**Status:** {status}\n\n"
        f"**Blocked by:** None\n\n"
        f"**Runner:** {runner}\n\n"
        f"## Acceptance criteria\n\n- [ ] criterion 1\n"
    )
    path = pathlib.Path(".scratch") / effort / "issues" / f"{number:02d}-t.md"
    return TicketParser().parse_text(text, filename=path.name, path=path)


def make_config(tmp_path: pathlib.Path, **kwargs) -> LocalWorkerConfig:
    return LocalWorkerConfig(
        repos=[LocalRepoEntry(path=str(tmp_path / "repo"), repo="owner/repo")],
        logs_dir=str(tmp_path / "logs"),
        **kwargs,
    )


class FakeGitHub:
    """Records every call and answers from small, test-set tables."""

    def __init__(self) -> None:
        self.claim_branches: list[str] = []
        self.claim_file_contents: dict[str, str] = {}
        self.repo_variable: str | None = None
        self.open_prs: dict[str, int] = {}  # ticket_branch -> pr number
        self.check_runs: dict[str, list[tuple[str, str]]] = {}
        self.open_issues: dict[str, list[dict]] = {}  # label -> [issue dicts]
        self._next_issue_number = 1000
        self.created_issues: list[dict] = []
        self.updated_bodies: list[dict] = []
        self.locked: list[int] = []
        self.pinned: list[int] = []
        self.closed_issues: list[int] = []

    def get_repo_variable(self, repo, name):
        return self.repo_variable

    def list_claim_branches(self, repo):
        return list(self.claim_branches)

    def get_file_contents(self, repo, path, ref=None):
        return {"content": self.claim_file_contents.get(ref, ""), "sha": "x"}

    def find_open_pr(self, repo, head_branch):
        return self.open_prs.get(head_branch)

    def list_check_runs(self, repo, ref):
        return self.check_runs.get(ref, [])

    def list_open_issues(self, repo, label):
        return list(self.open_issues.get(label, []))

    def find_open_issue(self, repo, label, title):
        for item in self.open_issues.get(label, []):
            if item.get("title") == title:
                return item["number"]
        return None

    def create_issue(self, repo, title, body, labels):
        number = self._next_issue_number
        self._next_issue_number += 1
        item = {"number": number, "title": title, "body": body}
        for label in labels:
            self.open_issues.setdefault(label, []).append(item)
        self.created_issues.append(
            {"repo": repo, "title": title, "body": body, "labels": labels, "number": number}
        )
        return number

    def lock_issue(self, repo, number):
        self.locked.append(number)
        return True

    def pin_issue(self, repo, number):
        self.pinned.append(number)
        return True

    def update_issue_body(self, repo, number, body):
        self.updated_bodies.append({"repo": repo, "number": number, "body": body})
        return {"number": number, "body": body}

    def close_issue(self, repo, number):
        self.closed_issues.append(number)
        for items in self.open_issues.values():
            items[:] = [i for i in items if i["number"] != number]
        return {"number": number, "state": "closed"}


class FakeLocalWorker:
    """A recording fake exposing run_one and fix_ci (ticket 31's test seam)."""

    def __init__(self) -> None:
        self.run_one_calls: list[dict] = []
        self.fix_ci_calls: list[dict] = []
        self._run_one_script: list[dict] = []
        self._fix_ci_script: list[dict] = []

    def script_run_one(self, outcomes: list[str], success: bool, reset_at=None) -> None:
        """Queue one run_one() call's behaviour: outcomes reported via on_outcome,
        then the final bool return."""
        self._run_one_script.append(
            {"outcomes": outcomes, "success": success, "reset_at": reset_at}
        )

    def script_fix_ci(self, outcome: str) -> None:
        self._fix_ci_script.append({"outcome": outcome})

    def run_one(self, entry, ticket, now=None, on_outcome=None):
        self.run_one_calls.append({"entry": entry, "ticket": ticket})
        script = self._run_one_script.pop(0)
        for outcome in script["outcomes"]:
            if on_outcome is not None:
                on_outcome(
                    SimpleNamespace(
                        outcome=outcome,
                        success=(outcome == "success"),
                        quota_error=(outcome == "quota"),
                        reset_at=script["reset_at"],
                    )
                )
        return script["success"]

    def fix_ci(self, entry, ticket, pr_number, on_outcome=None):
        self.fix_ci_calls.append({"entry": entry, "ticket": ticket, "pr_number": pr_number})
        script = self._fix_ci_script.pop(0)
        result = SimpleNamespace(
            outcome=script["outcome"],
            success=(script["outcome"] == "success"),
            quota_error=(script["outcome"] == "quota"),
            reset_at=None,
        )
        if on_outcome is not None:
            on_outcome(result)
        return result


def make_loop(
    tmp_path: pathlib.Path,
    github: FakeGitHub,
    worker: FakeLocalWorker,
    tickets: list[Ticket],
    now: datetime.datetime,
    config: LocalWorkerConfig | None = None,
) -> tuple[BoxLoop, dict]:
    clock = {"now": now}
    cfg = config or make_config(tmp_path)
    loop = BoxLoop(
        config=cfg,
        github_client=github,
        local_worker=worker,
        git_runner=lambda args, cwd, env: (0, ""),
        sleep_fn=lambda s: None,
        now_fn=lambda: clock["now"],
        ticket_loader=lambda path: list(tickets),
    )
    return loop, clock


# ---------------------------------------------------------------------------
# AC1: Entry point and config
# ---------------------------------------------------------------------------


def test_pyproject_registers_box_worker_console_script():
    pyproject = pathlib.Path(__file__).parent.parent / "pyproject.toml"
    text = pyproject.read_text(encoding="utf-8")
    assert 'box-worker = "ticket_engine.box_worker:main"' in text


def test_local_worker_config_gains_box_worker_fields(tmp_path):
    from ticket_engine.local_config import load_local_config

    config_file = tmp_path / "local.toml"
    config_file.write_text(
        "concurrency = 3\n"
        "poll_interval_minutes = 5\n"
        "status_interval_minutes = 15\n"
        "weekly_cap_after_hours = 4\n"
        "weekly_cap_backoff_hours = 6\n"
        'engine_repo = "someone/engine"\n'
        f'logs_dir = "{tmp_path / "logs"}"\n',
        encoding="utf-8",
    )
    cfg = load_local_config(config_file)
    assert cfg.concurrency == 3
    assert cfg.poll_interval_minutes == 5
    assert cfg.status_interval_minutes == 15
    assert cfg.weekly_cap_after_hours == 4
    assert cfg.weekly_cap_backoff_hours == 6
    assert cfg.engine_repo == "someone/engine"
    assert cfg.logs_dir == str(tmp_path / "logs")


def test_local_worker_config_box_worker_defaults():
    from ticket_engine.local_config import LocalWorkerConfig

    cfg = LocalWorkerConfig()
    assert cfg.concurrency == 1
    assert cfg.poll_interval_minutes == 10
    assert cfg.status_interval_minutes == 30
    assert cfg.weekly_cap_after_hours == 5
    assert cfg.weekly_cap_backoff_hours == 12
    assert cfg.engine_repo == "ilegault/ticket-engine"
    assert cfg.logs_dir.endswith(str(pathlib.Path("ticket-engine-box") / "logs"))


def test_main_once_runs_exactly_one_tick_and_returns_0(tmp_path):
    config_file = tmp_path / "local.toml"
    config_file.write_text(f'logs_dir = "{tmp_path / "logs"}"\n', encoding="utf-8")

    fake_loop = MagicMock()
    fake_loop.tick.return_value = None

    def loop_factory(local_cfg):
        assert local_cfg.logs_dir == str(tmp_path / "logs")
        return fake_loop

    rc = main(["--once", "--config", str(config_file)], loop_factory=loop_factory)

    assert rc == 0
    fake_loop.tick.assert_called_once()


# ---------------------------------------------------------------------------
# AC2: Tick builds the world and carries out the step
# ---------------------------------------------------------------------------


def test_claim_ticket_calls_run_one_and_appends_ledger(tmp_path):
    now = datetime.datetime(2026, 9, 26, 3, 0, tzinfo=datetime.UTC)
    t = ticket(1, "ready-for-agent")
    github = FakeGitHub()
    worker = FakeLocalWorker()
    worker.script_run_one(outcomes=["success"], success=True)

    loop, _clock = make_loop(tmp_path, github, worker, [t], now)
    # Force WriteStatus out of the way by pre-seeding last_status_write.
    loop._last_status_write = now

    step = loop.tick()

    from ticket_engine.box_core import ClaimTicket

    assert isinstance(step, ClaimTicket)
    assert len(worker.run_one_calls) == 1
    assert worker.run_one_calls[0]["ticket"].number == 1

    ledger = json.loads((tmp_path / "logs" / "ledger.json").read_text())
    assert ledger == [{"repo": "owner/repo", "ticket": 1, "started_at": ledger[0]["started_at"]}]
    started_at = datetime.datetime.fromisoformat(ledger[0]["started_at"])
    assert started_at == now


def test_resume_claim_calls_run_one_for_unfinished_box_claim(tmp_path):
    now = datetime.datetime(2026, 9, 26, 3, 0, tzinfo=datetime.UTC)
    t = ticket(1, "in-progress")
    github = FakeGitHub()
    github.claim_branches = ["claim/box-primary-worker/01"]
    github.claim_file_contents["claim/box-primary-worker/01"] = (
        "# 1: Ticket 1\n**Status:** in-progress\n**Claimed-by:** box\n"
    )
    worker = FakeLocalWorker()
    worker.script_run_one(outcomes=["success"], success=True)

    loop, _clock = make_loop(tmp_path, github, worker, [t], now)
    loop._last_status_write = now

    step = loop.tick()

    from ticket_engine.box_core import ResumeClaim

    assert isinstance(step, ResumeClaim)
    assert len(worker.run_one_calls) == 1
    assert worker.run_one_calls[0]["ticket"].number == 1
    assert not (tmp_path / "logs" / "ledger.json").exists()


def test_fix_ci_calls_local_worker_fix_ci(tmp_path):
    now = datetime.datetime(2026, 9, 26, 3, 0, tzinfo=datetime.UTC)
    t = ticket(1, "in-progress")
    github = FakeGitHub()
    github.claim_branches = ["claim/box-primary-worker/01"]
    github.claim_file_contents["claim/box-primary-worker/01"] = (
        "# 1: Ticket 1\n**Status:** in-progress\n**Claimed-by:** box\n"
    )
    github.open_prs["ticket/box-primary-worker-01-t"] = 55
    github.check_runs["ticket/box-primary-worker-01-t"] = [("integrity-gate", "failure")]
    worker = FakeLocalWorker()
    worker.script_fix_ci("success")

    loop, _clock = make_loop(tmp_path, github, worker, [t], now)
    loop._last_status_write = now

    step = loop.tick()

    from ticket_engine.box_core import FixCI

    assert isinstance(step, FixCI)
    assert len(worker.fix_ci_calls) == 1
    assert worker.fix_ci_calls[0]["pr_number"] == 55
    assert not worker.run_one_calls


def test_wait_step_sleeps_for_the_difference(tmp_path):
    now = datetime.datetime(2026, 9, 26, 3, 0, tzinfo=datetime.UTC)
    github = FakeGitHub()
    worker = FakeLocalWorker()
    sleeps: list[float] = []

    cfg = make_config(tmp_path)
    loop = BoxLoop(
        config=cfg,
        github_client=github,
        local_worker=worker,
        git_runner=lambda args, cwd, env: (0, ""),
        sleep_fn=sleeps.append,
        now_fn=lambda: now,
        ticket_loader=lambda path: [],
    )
    loop._last_status_write = now

    step = loop.tick()

    from ticket_engine.box_core import Wait

    assert isinstance(step, Wait)
    assert sleeps == [pytest.approx((step.until - now).total_seconds())]


# ---------------------------------------------------------------------------
# AC3 part 1: the status issue
# ---------------------------------------------------------------------------


def test_write_status_creates_locks_pins_and_updates_body(tmp_path):
    now = datetime.datetime(2026, 9, 26, 3, 12, tzinfo=datetime.UTC)
    github = FakeGitHub()
    worker = FakeLocalWorker()

    loop, _clock = make_loop(tmp_path, github, worker, [], now)
    step = loop.tick()

    from ticket_engine.box_core import WriteStatus

    assert isinstance(step, WriteStatus)
    assert len(github.created_issues) == 1
    created = github.created_issues[0]
    assert created["title"] == "Box status"
    assert created["labels"] == ["engine:box-status"]
    assert github.locked == [1000]
    assert github.pinned == [1000]

    assert len(github.updated_bodies) == 1
    body = github.updated_bodies[0]["body"]
    assert body == (
        "## Box status\n"
        "Checked in: 2026-09-26T03:12Z\n"
        "State: idle\n"
        "Current: none\n"
        "Paused until: none\n"
    )
    parsed = parse_box_status(body)
    assert parsed.state == BoxState.idle


def test_write_status_twice_creates_locks_pins_only_once(tmp_path):
    now = datetime.datetime(2026, 9, 26, 3, 12, tzinfo=datetime.UTC)
    github = FakeGitHub()
    worker = FakeLocalWorker()

    loop, clock = make_loop(tmp_path, github, worker, [], now)
    loop.tick()

    clock["now"] = now + datetime.timedelta(minutes=1)
    loop._last_status_write = now  # simulate a tick after the first WriteStatus
    step2 = loop.tick()

    from ticket_engine.box_core import Wait

    assert isinstance(step2, Wait)
    assert len(github.created_issues) == 1
    assert github.locked == [1000]
    assert github.pinned == [1000]


# ---------------------------------------------------------------------------
# AC4: quota and alerts
# ---------------------------------------------------------------------------


def test_quota_outcome_applies_after_quota_error_and_writes_pause_record(tmp_path):
    now = datetime.datetime(2026, 9, 26, 3, 0, tzinfo=datetime.UTC)
    t = ticket(1, "in-progress")
    github = FakeGitHub()
    github.claim_branches = ["claim/box-primary-worker/01"]
    github.claim_file_contents["claim/box-primary-worker/01"] = (
        "# 1: Ticket 1\n**Status:** in-progress\n**Claimed-by:** box\n"
    )
    worker = FakeLocalWorker()
    worker.script_run_one(outcomes=["quota"], success=False)

    loop, _clock = make_loop(tmp_path, github, worker, [t], now)
    loop._last_status_write = now

    loop.tick()

    record = json.loads((tmp_path / "logs" / "pause_record.json").read_text())
    assert record["quota_first_failure"] is not None
    assert record["quota_retry_at"] is not None
    retry_at = datetime.datetime.fromisoformat(record["quota_retry_at"])
    assert retry_at == now + datetime.timedelta(hours=1)


def test_success_outcome_applies_after_success_and_clears_pause_record(tmp_path):
    now = datetime.datetime(2026, 9, 26, 3, 0, tzinfo=datetime.UTC)
    t = ticket(1, "in-progress")
    github = FakeGitHub()
    github.claim_branches = ["claim/box-primary-worker/01"]
    github.claim_file_contents["claim/box-primary-worker/01"] = (
        "# 1: Ticket 1\n**Status:** in-progress\n**Claimed-by:** box\n"
    )
    worker = FakeLocalWorker()
    worker.script_run_one(outcomes=["success"], success=True)

    loop, _clock = make_loop(tmp_path, github, worker, [t], now)
    loop._logs_dir.mkdir(parents=True, exist_ok=True)
    loop._pause_record_path.write_text(
        json.dumps(
            {
                "quota_first_failure": (now - datetime.timedelta(hours=1)).isoformat(),
                "quota_retry_at": (now - datetime.timedelta(minutes=1)).isoformat(),
            }
        )
    )
    loop._last_status_write = now

    loop.tick()

    record = json.loads(loop._pause_record_path.read_text())
    assert record == {"quota_first_failure": None, "quota_retry_at": None}


def test_auth_outcome_raises_login_expired_alert(tmp_path):
    now = datetime.datetime(2026, 9, 26, 3, 0, tzinfo=datetime.UTC)
    t = ticket(1, "in-progress")
    github = FakeGitHub()
    github.claim_branches = ["claim/box-primary-worker/01"]
    github.claim_file_contents["claim/box-primary-worker/01"] = (
        "# 1: Ticket 1\n**Status:** in-progress\n**Claimed-by:** box\n"
    )
    worker = FakeLocalWorker()
    worker.script_run_one(outcomes=["auth"], success=False)

    loop, _clock = make_loop(tmp_path, github, worker, [t], now)
    loop._last_status_write = now

    loop.tick()

    assert len(github.created_issues) == 1
    assert github.created_issues[0]["title"] == "Box alert: agy login expired"
    assert github.created_issues[0]["labels"] == ["engine:box-alert"]


def test_raise_alert_is_idempotent_and_close_alert_closes_it(tmp_path):
    now = datetime.datetime(2026, 9, 26, 3, 0, tzinfo=datetime.UTC)
    github = FakeGitHub()
    worker = FakeLocalWorker()
    loop, _clock = make_loop(tmp_path, github, worker, [], now)

    loop._raise_alert(AlertKind.weekly_cap)
    loop._raise_alert(AlertKind.weekly_cap)
    assert len(github.created_issues) == 1

    loop._close_alert(AlertKind.weekly_cap)
    assert github.closed_issues == [github.created_issues[0]["number"]]
    assert loop._find_open_alert(AlertKind.weekly_cap) is None


def test_quota_failures_from_0h_to_5h01m_raise_one_weekly_cap_alert_then_success_closes_it(
    tmp_path,
):
    """Ticket 31 AC4: drives quota failures across a fake clock from 0h to
    5h01m and asserts exactly one weekly_cap issue was created, then a later
    success closes it."""
    start = datetime.datetime(2026, 9, 26, 0, 0, tzinfo=datetime.UTC)
    t = ticket(1, "in-progress")
    github = FakeGitHub()
    github.claim_branches = ["claim/box-primary-worker/01"]
    github.claim_file_contents["claim/box-primary-worker/01"] = (
        "# 1: Ticket 1\n**Status:** in-progress\n**Claimed-by:** box\n"
    )
    worker = FakeLocalWorker()

    loop, clock = make_loop(tmp_path, github, worker, [t], start)
    loop._last_status_write = start

    # Hourly quota failures at 0h, 1h, 2h, 3h, 4h, 5h01m. Pre-seed last_status_write
    # each tick so WriteStatus never preempts the quota rule under test (AC3).
    hours = [0, 1, 2, 3, 4]
    for h in hours:
        clock["now"] = start + datetime.timedelta(hours=h)
        loop._last_status_write = clock["now"]
        worker.script_run_one(outcomes=["quota"], success=False)
        step = loop.tick()
        assert step is not None

    clock["now"] = start + datetime.timedelta(hours=5, minutes=1)
    loop._last_status_write = clock["now"]
    worker.script_run_one(outcomes=["quota"], success=False)
    loop.tick()

    assert len(github.open_issues.get("engine:box-alert", [])) == 1
    assert github.open_issues["engine:box-alert"][0]["title"] == "Box alert: weekly cap reached"

    # A later success closes it.
    clock["now"] = start + datetime.timedelta(hours=17, minutes=2)
    loop._last_status_write = clock["now"]
    worker.script_run_one(outcomes=["success"], success=True)
    loop.tick()

    assert github.open_issues.get("engine:box-alert", []) == []


# ---------------------------------------------------------------------------
# AC5: logs stay local
# ---------------------------------------------------------------------------


def test_gitignore_lists_logs_dir():
    gitignore = pathlib.Path(__file__).parent.parent / ".gitignore"
    assert "logs/" in gitignore.read_text(encoding="utf-8").splitlines()


def test_configure_logging_writes_to_a_rotating_file_not_stdout(tmp_path, capsys):
    import logging

    from ticket_engine.box_worker import _configure_logging

    logs_dir = tmp_path / "logs"
    _configure_logging(logs_dir)
    logger = logging.getLogger("ticket_engine.box_worker.test")
    secret_text = "agy failed: raw traceback with SECRET_TOKEN_ABC"
    logger.error(secret_text)

    for h in list(logging.getLogger().handlers):
        h.flush()

    log_file = logs_dir / "box-worker.log"
    assert log_file.is_file()
    assert secret_text in log_file.read_text(encoding="utf-8")

    captured = capsys.readouterr()
    assert secret_text not in captured.out
    assert secret_text not in captured.err

    # Clean up the root logger handler so later tests are not affected.
    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler):
            root.removeHandler(h)
            h.close()


def test_fake_agy_failure_text_stays_in_the_log_and_out_of_github_request_bodies(tmp_path):
    """A fake agy failure's raw text appears in the log file and in no
    recorded GitHub request body (ADR 0007 rule 4)."""
    import logging

    from ticket_engine.box_worker import _configure_logging

    logs_dir = tmp_path / "logs"
    _configure_logging(logs_dir)

    now = datetime.datetime(2026, 9, 26, 3, 0, tzinfo=datetime.UTC)
    t = ticket(1, "in-progress")
    github = FakeGitHub()
    github.claim_branches = ["claim/box-primary-worker/01"]
    github.claim_file_contents["claim/box-primary-worker/01"] = (
        "# 1: Ticket 1\n**Status:** in-progress\n**Claimed-by:** box\n"
    )
    worker = FakeLocalWorker()
    worker.script_run_one(outcomes=["failed"], success=False)

    loop, _clock = make_loop(tmp_path, github, worker, [t], now)
    loop._last_status_write = now

    raw_agy_output = "Traceback (most recent call last): raw agy stack dump, secret detail"
    logging.getLogger("ticket_engine.local_worker").error(
        "agy failed for ticket %02d: %s", 1, raw_agy_output
    )

    loop.tick()

    for h in list(logging.getLogger().handlers):
        h.flush()

    log_file = logs_dir / "box-worker.log"
    assert raw_agy_output in log_file.read_text(encoding="utf-8")

    for created in github.created_issues:
        assert raw_agy_output not in created["body"]
    for updated in github.updated_bodies:
        assert raw_agy_output not in updated["body"]

    root = logging.getLogger()
    for h in list(root.handlers):
        if isinstance(h, logging.handlers.RotatingFileHandler):
            root.removeHandler(h)
            h.close()
