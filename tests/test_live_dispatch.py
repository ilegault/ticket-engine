"""Tests for live dispatch execution and adapter orchestration.

WHY THIS EXISTS
---------------
Ticket 06 Acceptance Criteria 1, 2, 4, 6, 7 mandate that:
1. Live dispatch creates a claim branch via the GitHub adapter, skipping tickets
   where the claim already exists.
2. For each claimed ticket, it creates a Jules session via the Jules adapter
   with the assembled prompt, repo, default branch, title, AUTO_CREATE_PR,
   and requirePlanApproval=False.
3. If TICKET_ENGINE_PAUSED is set, live dispatch starts nothing.
4. Prompts and API keys are never printed or logged to stdout/stderr.
5. All adapters are faked in tests with zero live network calls.
"""
from __future__ import annotations

import datetime
import urllib.error
from unittest.mock import MagicMock

from ticket_engine.box_status import BoxState, BoxStatus, render_box_status
from ticket_engine.config import RepoConfig
from ticket_engine.live_dispatch import LiveDispatcher
from ticket_engine.parser import Ticket


def make_ticket(
    number: int,
    title: str = "Test ticket",
    status: str = "ready-for-agent",
    effort: str = "phase-1",
    runner: str = "any",
) -> Ticket:
    return Ticket(
        number=number,
        title=title,
        slug=f"test-ticket-{number}",
        status=status,
        runner=runner,
        effort=effort,
        blocked_by=[],
    )


def test_live_dispatch_claims_and_starts_jules_session(capsys):
    # Fakes
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "base123sha"
    mock_github.create_claim_branch.return_value = True  # Successfully creates claim

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 5  # Well below reserve
    mock_jules.create_session.return_value = {"id": "sessions/test123", "state": "RUNNING"}

    config = RepoConfig(default_branch="main", concurrency=2, daily_cap=10)
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=config,
    )

    tickets = [
        make_ticket(1, title="First Feature"),
        make_ticket(2, title="Second Feature"),
    ]

    started = dispatcher.dispatch(tickets=tickets)

    assert len(started) == 2
    # Verify GitHub claim branches created
    assert mock_github.create_claim_branch.call_count == 2
    mock_github.create_claim_branch.assert_any_call(
        repo="owner/repo",
        effort="phase-1",
        ticket_number=1,
        sha="base123sha",
    )
    mock_github.create_claim_branch.assert_any_call(
        repo="owner/repo",
        effort="phase-1",
        ticket_number=2,
        sha="base123sha",
    )

    # Verify Jules sessions created
    assert mock_jules.create_session.call_count == 2
    first_call_kwargs = mock_jules.create_session.call_args_list[0].kwargs
    assert first_call_kwargs["repo"] == "owner/repo"
    assert first_call_kwargs["starting_branch"] == "main"
    assert first_call_kwargs["title"] == "phase-1-01: First Feature"
    assert first_call_kwargs["automation_mode"] == "AUTO_CREATE_PR"
    assert first_call_kwargs["require_plan_approval"] is False
    assert "ORIENT" in first_call_kwargs["prompt"]

    # Verify nothing leaked to stdout/stderr (ADR 0002)
    captured = capsys.readouterr()
    assert "mock_jules_key" not in captured.out
    assert first_call_kwargs["prompt"] not in captured.out


def test_live_dispatch_claim_collision_skips_and_proceeds_to_next():
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "base123sha"

    # Ticket 1 claim fails (already claimed -> 422), Ticket 2 claim succeeds
    def mock_create_claim(repo, effort, ticket_number, sha):
        return ticket_number != 1

    mock_github.create_claim_branch.side_effect = mock_create_claim

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 5
    mock_jules.create_session.return_value = {"id": "sessions/test456", "state": "RUNNING"}

    config = RepoConfig(default_branch="master", concurrency=2)
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=config,
    )

    tickets = [
        make_ticket(1, title="Claim Collision Ticket"),
        make_ticket(2, title="Available Ticket"),
    ]

    started = dispatcher.dispatch(tickets=tickets)

    # Only Ticket 2 started
    assert len(started) == 1
    assert started[0].number == 2
    mock_jules.create_session.assert_called_once()
    assert mock_jules.create_session.call_args.kwargs["title"] == "phase-1-02: Available Ticket"


def test_live_dispatch_paused_starts_nothing():
    mock_github = MagicMock()
    mock_jules = MagicMock()

    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        paused=True,
    )

    tickets = [make_ticket(1)]
    started = dispatcher.dispatch(tickets=tickets)

    assert len(started) == 0
    mock_github.create_claim_branch.assert_not_called()
    mock_jules.create_session.assert_not_called()


def test_live_dispatch_releases_claim_for_done_ticket_with_no_live_session():
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "base123sha"
    mock_github.list_claim_branches.return_value = ["claim/phase-1/01"]
    mock_github.create_claim_branch.return_value = True

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 5
    mock_jules.list_sessions.return_value = []
    mock_jules.create_session.return_value = {"id": "sessions/test789", "state": "RUNNING"}

    config = RepoConfig(default_branch="master", concurrency=2)
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=config,
    )

    tickets = [
        make_ticket(1, title="Already Shipped", status="done"),
        make_ticket(2, title="Next Up"),
    ]

    started = dispatcher.dispatch(tickets=tickets)

    # The stale claim for the done ticket 1 was deleted...
    mock_github.delete_branch.assert_called_once_with(repo="owner/repo", branch="claim/phase-1/01")
    # ...which freed a concurrency slot so ticket 2 could start.
    assert len(started) == 1
    assert started[0].number == 2


def test_live_dispatch_does_not_release_claim_with_live_jules_session():
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "base123sha"
    mock_github.list_claim_branches.return_value = ["claim/phase-1/01"]

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 5
    mock_jules.list_sessions.return_value = [
        {"id": "sessions/live1", "state": "RUNNING", "title": "phase-1-01: Still Going"}
    ]

    config = RepoConfig(default_branch="master", concurrency=2)
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=config,
    )

    # Ticket file already flipped to done (PR merged) but Jules is still
    # mid-session on it (e.g. a follow-up fix commit) - must not release yet.
    tickets = [make_ticket(1, title="Still In Flight", status="done")]

    dispatcher.dispatch(tickets=tickets)

    mock_github.delete_branch.assert_not_called()


def test_live_dispatch_writes_claimed_by_jules_to_claim_branch():
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "base123sha"
    mock_github.create_claim_branch.return_value = True
    mock_github.get_file_contents.return_value = {
        "content": "# 01: Test ticket\n\n**Status:** ready-for-agent\n\n**Runner:** any\n",
        "sha": "file_sha_123",
    }

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 5
    mock_jules.create_session.return_value = {"id": "sessions/test123", "state": "RUNNING"}

    call_order: list[str] = []
    mock_github.commit_file_change.side_effect = lambda **kwargs: call_order.append("commit")
    mock_jules.create_session.side_effect = lambda **kwargs: (
        call_order.append("session"),
        {"id": "sessions/test123", "state": "RUNNING"},
    )[1]

    config = RepoConfig(default_branch="main", concurrency=2, daily_cap=10)
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=config,
    )

    tickets = [make_ticket(1, title="Test ticket", effort="phase-1")]
    started = dispatcher.dispatch(tickets=tickets)

    assert len(started) == 1
    mock_github.commit_file_change.assert_called_once()
    commit_kwargs = mock_github.commit_file_change.call_args.kwargs
    assert commit_kwargs["repo"] == "owner/repo"
    assert commit_kwargs["branch"] == "claim/phase-1/01"
    assert commit_kwargs["message"] == "Claim 01 for jules"
    assert commit_kwargs["sha"] == "file_sha_123"
    assert "**Status:** ready-for-agent\n\n**Claimed-by:** jules" in commit_kwargs["content"]
    assert call_order == ["commit", "session"]


def test_live_dispatch_creates_session_even_if_claimed_by_commit_fails(caplog):
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "base123sha"
    mock_github.create_claim_branch.return_value = True
    mock_github.get_file_contents.side_effect = urllib.error.HTTPError(
        "http://api.github.com", 500, "Server Error", {}, None
    )

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 5
    mock_jules.create_session.return_value = {"id": "sessions/test123", "state": "RUNNING"}

    config = RepoConfig(default_branch="main", concurrency=2, daily_cap=10)
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=config,
    )

    tickets = [make_ticket(1, title="Test ticket", effort="phase-1")]
    with caplog.at_level("ERROR"):
        started = dispatcher.dispatch(tickets=tickets)

    assert len(started) == 1
    mock_jules.create_session.assert_called_once()
    assert any("Failed to record Claimed-by: jules" in record.message for record in caplog.records)


def test_live_dispatch_releases_stale_box_claim_at_9_hours():
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "base123sha"
    mock_github.list_claim_branches.return_value = ["claim/box-primary-worker/01"]
    mock_github.get_file_contents.return_value = {
        "content": "# 01: Test ticket\n\n**Status:** in-progress\n\n**Claimed-by:** box\n\n**Blocked by:** None\n",
        "sha": "sha1",
    }
    now = datetime.datetime.now(datetime.UTC)
    mock_github.get_branch_head_time.return_value = now - datetime.timedelta(hours=9)

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 0
    mock_jules.list_sessions.return_value = []

    config = RepoConfig(default_branch="main", concurrency=2, box_stale_claim_hours=8)
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=config,
    )

    ticket1 = make_ticket(1, title="Test ticket", effort="box-primary-worker", status="in-progress")
    dispatcher.dispatch(tickets=[ticket1])

    # Claim was released with delete_branch
    mock_github.delete_branch.assert_called_with(repo="owner/repo", branch="claim/box-primary-worker/01")
    assert (1, "Stale box claim: no checkpoint in 8 hours") in dispatcher.last_run.released_stale


def test_live_dispatch_keeps_box_claim_at_1_hour():
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "base123sha"
    mock_github.list_claim_branches.return_value = ["claim/box-primary-worker/01"]
    mock_github.get_file_contents.return_value = {
        "content": "# 01: Test ticket\n\n**Status:** in-progress\n\n**Claimed-by:** box\n\n**Blocked by:** None\n",
        "sha": "sha1",
    }
    now = datetime.datetime.now(datetime.UTC)
    mock_github.get_branch_head_time.return_value = now - datetime.timedelta(hours=1)

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 0
    mock_jules.list_sessions.return_value = []

    config = RepoConfig(default_branch="main", concurrency=2, box_stale_claim_hours=8)
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=config,
    )

    ticket1 = make_ticket(1, title="Test ticket", effort="box-primary-worker", status="in-progress")
    dispatcher.dispatch(tickets=[ticket1])

    mock_github.delete_branch.assert_not_called()
    assert dispatcher.last_run.released_stale == []




def test_live_dispatch_reads_box_status_when_enabled():
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "base123sha"
    now = datetime.datetime.now(datetime.UTC)
    box_body = render_box_status(
        BoxStatus(
            checked_in_at=now - datetime.timedelta(hours=1),
            state=BoxState.idle,
        )
    )
    mock_github.list_issues.return_value = [
        {
            "number": 100,
            "title": "Box Status",
            "body": box_body,
        }
    ]

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 5

    config = RepoConfig(default_branch="main", concurrency=2, box_enabled=True)
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=config,
    )

    t1 = make_ticket(1, title="First Feature")
    started = dispatcher.dispatch(tickets=[t1])

    # Box is available -> starts 0, left for box
    assert len(started) == 0
    mock_github.list_issues.assert_called_once_with(
        repo="ilegault/ticket-engine",
        state="open",
        labels="engine:box-status",
    )
    assert dispatcher.last_run.box_state == "available"
    assert dispatcher.last_run.left_for_box == [t1]
    mock_jules.create_session.assert_not_called()


def test_live_dispatch_box_status_read_failure_sets_box_none_and_never_raises():
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "base123sha"
    mock_github.create_claim_branch.return_value = True
    mock_github.list_issues.side_effect = urllib.error.HTTPError(
        "http://api.github.com", 500, "Server Error", {}, None
    )

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 5
    mock_jules.create_session.return_value = {"id": "sessions/test123", "state": "RUNNING"}

    config = RepoConfig(default_branch="main", concurrency=2, box_enabled=True)
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=config,
    )

    t1 = make_ticket(1, title="First Feature")
    started = dispatcher.dispatch(tickets=[t1])

    assert len(started) == 1
    assert dispatcher.last_run.box_state == "unreadable"
    assert "Server Error" in dispatcher.last_run.box_status_error
    mock_jules.create_session.assert_called_once()


def test_live_dispatch_box_disabled_passes_no_box_and_makes_no_issues_request():
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "base123sha"
    mock_github.create_claim_branch.return_value = True

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 5
    mock_jules.create_session.return_value = {"id": "sessions/test123", "state": "RUNNING"}

    config = RepoConfig(default_branch="main", concurrency=2, box_enabled=False)
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=config,
    )

    t1 = make_ticket(1, title="First Feature")
    started = dispatcher.dispatch(tickets=[t1])

    assert len(started) == 1
    mock_github.list_issues.assert_not_called()
    assert dispatcher.last_run.box_state == "none"
    mock_jules.create_session.assert_called_once()
