"""Tests for the scheduled box-silent check.

WHY THIS EXISTS
---------------
Ticket 33 (§Held changes), ADR 0007 rule 4: a scheduled workflow must notice
when the box's heartbeat has gone silent and @mention the developer, then
close that alert once the box checks in again. This exercises the pure
decision function `box_status.silent_check_action` and the script
`scripts/check_box_silent.py`, whose GitHub calls go through a fake client
that records what it was asked to do.
"""
from __future__ import annotations

import datetime
import pathlib

import pytest

from ticket_engine.box_status import BoxState, BoxStatus, silent_check_action

_MOD_PATH = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "check_box_silent.py"


def _load_check_box_silent():
    """Import scripts/check_box_silent.py as a module (scripts/ has no package)."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_box_silent", _MOD_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


check_box_silent = _load_check_box_silent()


_NOW = datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.UTC)


def _status(checked_in_at: datetime.datetime) -> BoxStatus:
    return BoxStatus(checked_in_at=checked_in_at, state=BoxState.working)


class FakeGitHubClient:
    """Records the calls `check_box_silent.main` makes, and answers the reads."""

    def __init__(self, status_issue_body: str | None, alert_issue_number: int | None):
        self._status_issue_body = status_issue_body
        self._alert_issue_number = alert_issue_number
        self.calls: list[tuple[str, tuple]] = []

    def list_open_issues(self, repo: str, label: str) -> list[dict]:
        self.calls.append(("list_open_issues", (repo, label)))
        if self._status_issue_body is None:
            return []
        return [{"body": self._status_issue_body}]

    def find_open_issue(self, repo: str, label: str, title: str) -> int | None:
        self.calls.append(("find_open_issue", (repo, label, title)))
        return self._alert_issue_number

    def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> int:
        self.calls.append(("create_issue", (repo, title, body, tuple(labels))))
        return 99

    def close_issue(self, repo: str, number: int) -> dict:
        self.calls.append(("close_issue", (repo, number)))
        return {"state": "closed"}


# --- Pure decision: box_status.silent_check_action ---------------------------


def test_silent_check_action_opens_when_status_missing_and_no_alert():
    assert silent_check_action(None, _NOW, 12, alert_open=False) == "open"


def test_silent_check_action_none_when_status_missing_but_alert_open():
    assert silent_check_action(None, _NOW, 12, alert_open=True) == "none"


def test_silent_check_action_not_silent_at_11h59():
    status = _status(_NOW - datetime.timedelta(hours=11, minutes=59))
    assert silent_check_action(status, _NOW, 12, alert_open=False) == "none"
    assert silent_check_action(status, _NOW, 12, alert_open=True) == "close"


def test_silent_check_action_silent_at_12h00():
    status = _status(_NOW - datetime.timedelta(hours=12))
    assert silent_check_action(status, _NOW, 12, alert_open=False) == "open"
    assert silent_check_action(status, _NOW, 12, alert_open=True) == "none"


# --- Script: scripts/check_box_silent.py --------------------------------------


def test_main_opens_alert_when_box_silent_and_no_alert_open(capsys):
    old_checkin = _NOW - datetime.timedelta(hours=13)
    body = f"## Box status\nChecked in: {old_checkin.strftime('%Y-%m-%dT%H:%MZ')}\nState: working\nCurrent: none\nPaused until: none\n"
    client = FakeGitHubClient(status_issue_body=body, alert_issue_number=None)

    rc = check_box_silent.main(client, "acme/engine", _NOW)

    assert rc == 0
    create_calls = [c for c in client.calls if c[0] == "create_issue"]
    assert len(create_calls) == 1
    _, (repo, title, issue_body, labels) = create_calls[0]
    assert repo == "acme/engine"
    assert title == "Box alert: box silent"
    assert issue_body.startswith("@acme\n")
    assert labels == ("engine:box-alert",)
    assert not any(c[0] == "close_issue" for c in client.calls)
    out = capsys.readouterr().out.strip()
    assert out == "box silent alert opened"


def test_main_closes_alert_when_box_checked_in_and_alert_open(capsys):
    recent_checkin = _NOW - datetime.timedelta(hours=1)
    body = f"## Box status\nChecked in: {recent_checkin.strftime('%Y-%m-%dT%H:%MZ')}\nState: working\nCurrent: none\nPaused until: none\n"
    client = FakeGitHubClient(status_issue_body=body, alert_issue_number=42)

    rc = check_box_silent.main(client, "acme/engine", _NOW)

    assert rc == 0
    close_calls = [c for c in client.calls if c[0] == "close_issue"]
    assert close_calls == [("close_issue", ("acme/engine", 42))]
    assert not any(c[0] == "create_issue" for c in client.calls)
    out = capsys.readouterr().out.strip()
    assert out == "box silent alert closed"


def test_main_no_change_when_box_available_and_no_alert_open(capsys):
    recent_checkin = _NOW - datetime.timedelta(hours=1)
    body = f"## Box status\nChecked in: {recent_checkin.strftime('%Y-%m-%dT%H:%MZ')}\nState: working\nCurrent: none\nPaused until: none\n"
    client = FakeGitHubClient(status_issue_body=body, alert_issue_number=None)

    rc = check_box_silent.main(client, "acme/engine", _NOW)

    assert rc == 0
    assert not any(c[0] in ("create_issue", "close_issue") for c in client.calls)
    out = capsys.readouterr().out.strip()
    assert out == "no change"


def test_main_no_change_when_box_silent_and_alert_already_open(capsys):
    old_checkin = _NOW - datetime.timedelta(hours=20)
    body = f"## Box status\nChecked in: {old_checkin.strftime('%Y-%m-%dT%H:%MZ')}\nState: working\nCurrent: none\nPaused until: none\n"
    client = FakeGitHubClient(status_issue_body=body, alert_issue_number=42)

    rc = check_box_silent.main(client, "acme/engine", _NOW)

    assert rc == 0
    assert not any(c[0] in ("create_issue", "close_issue") for c in client.calls)
    out = capsys.readouterr().out.strip()
    assert out == "no change"


def test_main_treats_unreadable_status_as_silent(capsys):
    client = FakeGitHubClient(status_issue_body="not a box status body", alert_issue_number=None)

    rc = check_box_silent.main(client, "acme/engine", _NOW)

    assert rc == 0
    create_calls = [c for c in client.calls if c[0] == "create_issue"]
    assert len(create_calls) == 1
    out = capsys.readouterr().out.strip()
    assert out == "box silent alert opened"


# --- Public text only: only the three fixed status lines are printed ---------


@pytest.mark.parametrize(
    ("status_body", "alert_issue_number", "expected_line"),
    [
        (None, None, "box silent alert opened"),
        (
            "## Box status\nChecked in: 2026-01-01T11:00Z\nState: working\nCurrent: none\nPaused until: none\n",
            42,
            "box silent alert closed",
        ),
        (
            "## Box status\nChecked in: 2026-01-01T11:00Z\nState: working\nCurrent: none\nPaused until: none\n",
            None,
            "no change",
        ),
    ],
)
def test_script_prints_only_fixed_status_lines(capsys, status_body, alert_issue_number, expected_line):
    client = FakeGitHubClient(status_issue_body=status_body, alert_issue_number=alert_issue_number)

    check_box_silent.main(client, "acme/engine", _NOW)

    out = capsys.readouterr().out.strip()
    assert out in ("box silent alert opened", "box silent alert closed", "no change")
    assert out == expected_line


# --- Workflow file -------------------------------------------------------------


def test_workflow_has_schedule_permissions_and_script_invocation():
    workflow_path = (
        pathlib.Path(__file__).resolve().parent.parent
        / ".github"
        / "workflows"
        / "box-silent-check.yml"
    )
    text = workflow_path.read_text()

    assert "cron: '41 */3 * * *'" in text
    assert "workflow_dispatch:" in text
    assert "issues: write" in text
    assert "contents: read" in text
    assert "actions/checkout@v4" in text
    assert "actions/setup-python@v5" in text
    assert 'python-version: "3.12"' in text
    assert "pip install -e ." in text
    assert "python scripts/check_box_silent.py" in text
    assert "GITHUB_TOKEN" in text
    assert "GITHUB_REPOSITORY" in text
