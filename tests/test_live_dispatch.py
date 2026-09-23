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

from unittest.mock import MagicMock

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
