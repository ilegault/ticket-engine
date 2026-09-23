"""Tests for ticket escalation, brief assembly, and live escalation execution.

WHY THIS EXISTS
---------------
Phase 1 Spec §Fix attempts and escalation and Ticket 07 Acceptance Criteria 1, 2, 3, 4
mandate that:
1. The dispatcher counts failed CI runs across a PR's successive head commits; after the third it escalates.
2. Escalating commits the ticket's `Status: blocked` and the escalation brief under `## Comments`
   to the PR branch, converts the PR to draft, and applies `engine:escalated`.
3. The brief is assembled from the ticket, the Jules session's activities and the failing CI log excerpt,
   in the format the ticket skill defines.
4. Two escalations in one repo within 24 hours set `TICKET_ENGINE_PAUSED`; clearing it resumes dispatch on the next run.
"""
from __future__ import annotations

import base64
import datetime
from unittest.mock import MagicMock, patch

from ticket_engine.config import RepoConfig
from ticket_engine.dispatch import (
    Claim,
    OpenPR,
    PauseRepoAction,
    apply_escalation_to_ticket_text,
    assemble_escalation_brief,
)
from ticket_engine.github import GitHubClient
from ticket_engine.live_dispatch import LiveDispatcher
from ticket_engine.parser import Ticket


def make_ticket(
    number: int,
    title: str = "Test ticket",
    status: str = "in-progress",
    effort: str = "phase-1",
    what_to_build: str = "Implement test feature.",
    raw_text: str = "",
) -> Ticket:
    if not raw_text:
        raw_text = (
            f"# {number:02d}: {title}\n\n"
            f"**What to build:** {what_to_build}\n\n"
            f"**Blocked by:** None\n\n"
            f"**Status:** {status}\n\n"
            f"**Runner:** any\n\n"
            f"**Auto-merge:** no\n\n"
            f"## Acceptance criteria\n\n"
            f"- [ ] Criterion 1\n\n"
            f"## Comments\n"
        )
    return Ticket(
        number=number,
        title=title,
        slug=f"test-ticket-{number}",
        status=status,
        runner="any",
        effort=effort,
        blocked_by=[],
        raw_text=raw_text,
    )


def test_assemble_escalation_brief_matches_ticket_skill_format():
    ticket = make_ticket(
        7,
        title="Escalation circuit breaker",
        what_to_build="When a ticket cannot be made to pass honestly, it stops consuming quota.",
    )
    activities = [
        {"description": "First fix attempt", "result": "AssertionError in test_math"},
        {"description": "Second fix attempt", "result": "Timeout in gate"},
        {"description": "Third fix attempt", "result": "Integrity check failed"},
    ]
    ci_log = "FAILED tests/test_core.py::test_eval - AssertionError: expected True got False"
    brief = assemble_escalation_brief(
        ticket=ticket,
        branch="ticket/phase-1-07-escalation",
        activities=activities,
        ci_log_excerpt=ci_log,
        date="2026-09-23",
        decision_needed="Should we relax tolerance or refactor the test fixture?",
    )

    expected_lines = [
        "## Escalation — 2026-09-23",
        "Ticket: 07 Escalation circuit breaker   Branch: ticket/phase-1-07-escalation",
        "Goal: When a ticket cannot be made to pass honestly, it stops consuming quota.",
        "Attempt 1: First fix attempt → AssertionError in test_math",
        "Attempt 2: Second fix attempt → Timeout in gate",
        "Attempt 3: Third fix attempt → Integrity check failed",
        "Failing output (exact, trimmed to the relevant lines):",
        "```",
        ci_log,
        "```",
        "Decision needed: Should we relax tolerance or refactor the test fixture?",
    ]
    for line in expected_lines:
        assert line in brief


def test_apply_escalation_to_ticket_text_updates_status_and_appends_brief():
    original_text = (
        "# 07: Escalation circuit breaker\n\n"
        "**What to build:** Stop quota consumption.\n\n"
        "**Status:** in-progress\n\n"
        "## Acceptance criteria\n\n"
        "- [ ] Criterion 1\n\n"
        "## Comments\n\n"
        "Some existing progress note.\n"
    )
    brief = "## Escalation — 2026-09-23\nTicket: 07\nGoal: Stop quota\n"
    updated = apply_escalation_to_ticket_text(original_text, brief)

    assert "**Status:** blocked" in updated
    assert "**Status:** in-progress" not in updated
    assert "## Comments" in updated
    assert brief in updated


def test_live_dispatch_orchestrates_escalation_commit_draft_and_label():
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "sha123"
    mock_github.list_claim_branches.return_value = []
    mock_github.get_file_contents.return_value = {
        "content": (
            "# 07: Feature\n\n"
            "**What to build:** Test feature.\n\n"
            "**Status:** in-progress\n\n"
            "## Comments\n"
        ),
        "sha": "file_sha_123",
    }
    mock_github.commit_file_change.return_value = True
    mock_github.convert_pr_to_draft.return_value = True
    mock_github.add_issue_labels.return_value = True

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 0
    mock_jules.list_sessions.return_value = []

    ticket = make_ticket(7, title="Failing Feature")
    open_pr = OpenPR(
        number=42,
        branch="ticket/phase-1-07-failing-feature",
        ticket_number=7,
        failed_ci_count=3,
        ci_log_excerpt="pytest failure output",
    )

    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=RepoConfig(default_branch="master"),
    )

    # Dispatch should detect PR with 3 failed CI runs and escalate
    dispatcher.dispatch_escalations_and_stale_claims(
        tickets=[ticket],
        open_prs=[open_pr],
    )

    # 1. Ticket status updated and brief committed to PR branch
    mock_github.commit_file_change.assert_called_once()
    commit_call = mock_github.commit_file_change.call_args.kwargs
    assert commit_call["repo"] == "owner/repo"
    assert commit_call["branch"] == "ticket/phase-1-07-failing-feature"
    assert "Status: blocked" in commit_call["content"] or "**Status:** blocked" in commit_call["content"]
    assert "## Escalation" in commit_call["content"]

    # 2. PR converted to draft
    mock_github.convert_pr_to_draft.assert_called_once_with(
        repo="owner/repo",
        pr_number=42,
    )

    # 3. Label applied
    mock_github.add_issue_labels.assert_called_once_with(
        repo="owner/repo",
        issue_number=42,
        labels=["engine:escalated"],
    )


def test_live_dispatch_releases_stale_claim_via_github():
    mock_github = MagicMock()
    mock_github.delete_branch.return_value = True

    mock_jules = MagicMock()

    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
    )

    now = datetime.datetime(2026, 9, 22, 12, 0, 0, tzinfo=datetime.UTC)
    stale_claim = Claim(
        ref="claim/phase-1/01",
        ticket_number=1,
        effort="phase-1",
        last_commit_time=now - datetime.timedelta(hours=14),
        has_live_session=False,
    )

    actions = dispatcher.dispatch_escalations_and_stale_claims(
        tickets=[make_ticket(1)],
        claims=[stale_claim],
        now=now,
    )

    assert len(actions) == 1
    mock_github.delete_branch.assert_called_once_with(
        repo="owner/repo",
        branch="claim/phase-1/01",
    )


def test_live_dispatch_trips_circuit_breaker_and_sets_paused_variable():
    mock_github = MagicMock()
    mock_github.get_repo_variable.return_value = None  # initially not paused
    mock_github.set_repo_variable.return_value = True

    mock_jules = MagicMock()

    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        config=RepoConfig(circuit_breaker_escalations_limit=2),
    )

    now = datetime.datetime(2026, 9, 22, 12, 0, 0, tzinfo=datetime.UTC)
    recent_escalations = [
        now - datetime.timedelta(hours=2),
        now - datetime.timedelta(hours=1),
    ]

    actions = dispatcher.dispatch_escalations_and_stale_claims(
        tickets=[make_ticket(1)],
        escalations=recent_escalations,
        now=now,
    )

    assert any(isinstance(a, PauseRepoAction) for a in actions)
    mock_github.set_repo_variable.assert_called_once_with(
        repo="owner/repo",
        name="TICKET_ENGINE_PAUSED",
        value="true",
    )
    assert dispatcher.paused is True


def test_clearing_paused_variable_resumes_dispatch_on_next_run():
    mock_github = MagicMock()
    mock_github.get_default_branch_sha.return_value = "sha_base"
    mock_github.list_claim_branches.return_value = []
    mock_github.create_claim_branch.return_value = True

    mock_jules = MagicMock()
    mock_jules.count_recent_sessions.return_value = 0
    mock_jules.list_sessions.return_value = []
    mock_jules.create_session.return_value = {"id": "sess1"}

    # Start with paused=True
    dispatcher = LiveDispatcher(
        repo="owner/repo",
        github_client=mock_github,
        jules_client=mock_jules,
        paused=True,
    )

    # 1. While variable is "true", dispatch does nothing
    mock_github.get_repo_variable.return_value = "true"
    started_paused = dispatcher.dispatch([make_ticket(1, status="ready-for-agent")])
    assert len(started_paused) == 0
    mock_jules.create_session.assert_not_called()

    # 2. Developer clears variable (get_repo_variable returns None)
    mock_github.get_repo_variable.return_value = None
    started_resumed = dispatcher.dispatch([make_ticket(1, status="ready-for-agent")])
    assert len(started_resumed) == 1
    assert started_resumed[0].number == 1
    mock_jules.create_session.assert_called_once()
    assert dispatcher.paused is False


def test_github_client_methods_for_escalation_and_claims():
    client = GitHubClient(token="mock_token")

    # 1. get_file_contents
    encoded_hello = base64.b64encode(b"hello world").decode("ascii")
    with patch.object(client, "_request", return_value={"content": encoded_hello, "encoding": "base64", "sha": "sh1"}):
        res = client.get_file_contents("owner/repo", "path/to/file.md", ref="branch1")
        assert res["content"] == "hello world"
        assert res["sha"] == "sh1"

    # 2. commit_file_change
    with patch.object(client, "_request", return_value={"commit": {"sha": "csha"}}) as mock_req:
        res = client.commit_file_change(
            "owner/repo", "path/file.md", "new text", "message", "branch1", sha="sh1"
        )
        assert res["commit"]["sha"] == "csha"
        mock_req.assert_called_once()
        assert mock_req.call_args[0][0] == "PUT"

    # 3. convert_pr_to_draft
    with patch.object(client, "_request", return_value={"draft": True}) as mock_req:
        res = client.convert_pr_to_draft("owner/repo", 42)
        assert res["draft"] is True
        mock_req.assert_called_once_with("PATCH", "/repos/owner/repo/pulls/42", {"draft": True})

    # 4. add_issue_labels
    with patch.object(client, "_request", return_value=[{"name": "engine:escalated"}]) as mock_req:
        res = client.add_issue_labels("owner/repo", 42, ["engine:escalated"])
        assert res == ["engine:escalated"]
        mock_req.assert_called_once_with("POST", "/repos/owner/repo/issues/42/labels", {"labels": ["engine:escalated"]})

    # 5. delete_branch
    with patch.object(client, "_request", return_value=None) as mock_req:
        res = client.delete_branch("owner/repo", "claim/phase-1/01")
        assert res is True
        mock_req.assert_called_once_with("DELETE", "/repos/owner/repo/git/refs/heads/claim/phase-1/01")

    # 6. set_repo_variable
    with patch.object(client, "_request", return_value=None) as mock_req:
        res = client.set_repo_variable("owner/repo", "TICKET_ENGINE_PAUSED", "true")
        assert res is True
        mock_req.assert_called_once_with("PATCH", "/repos/owner/repo/actions/variables/TICKET_ENGINE_PAUSED", {"name": "TICKET_ENGINE_PAUSED", "value": "true"})

