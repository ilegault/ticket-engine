"""Tests for GitHub REST API adapter.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Adapters) and Ticket 03 Acceptance Criterion 5
require posting the integrity verdict as a PR comment (updated, not duplicated)
and setting check status on the commit.
All adapter operations are tested against faked HTTP responses with zero live network calls.
"""
from __future__ import annotations

from unittest.mock import patch

from ticket_engine.github import (
    INTEGRITY_COMMENT_MARKER,
    GitHubClient,
)


def test_post_or_update_pr_comment_creates_when_no_existing_comment():
    client = GitHubClient(token="fake-token")

    existing_comments = [
        {"id": 101, "body": "Regular comment by user"},
    ]
    created_comment = {"id": 102, "body": f"{INTEGRITY_COMMENT_MARKER}\nVerdict: pass"}

    with patch.object(client, "_request") as mock_req:
        mock_req.side_effect = [
            existing_comments,  # GET /issues/42/comments
            created_comment,    # POST /issues/42/comments
        ]

        result = client.post_or_update_pr_comment(
            repo="owner/repo",
            pr_number=42,
            body="Verdict: pass",
        )

        assert result["id"] == 102
        assert mock_req.call_count == 2

        # Verify POST was called
        method, endpoint, payload = mock_req.call_args_list[1][0]
        assert method == "POST"
        assert endpoint == "/repos/owner/repo/issues/42/comments"
        assert INTEGRITY_COMMENT_MARKER in payload["body"]


def test_post_or_update_pr_comment_updates_when_existing_comment_found():
    client = GitHubClient(token="fake-token")

    existing_comments = [
        {"id": 101, "body": "Regular comment"},
        {"id": 202, "body": f"{INTEGRITY_COMMENT_MARKER}\nOld verdict: fail"},
    ]
    updated_comment = {"id": 202, "body": f"{INTEGRITY_COMMENT_MARKER}\nNew verdict: pass"}

    with patch.object(client, "_request") as mock_req:
        mock_req.side_effect = [
            existing_comments,  # GET /issues/42/comments
            updated_comment,    # PATCH /issues/comments/202
        ]

        result = client.post_or_update_pr_comment(
            repo="owner/repo",
            pr_number=42,
            body="New verdict: pass",
        )

        assert result["id"] == 202
        assert mock_req.call_count == 2

        # Verify PATCH was called on existing comment 202
        method, endpoint, payload = mock_req.call_args_list[1][0]
        assert method == "PATCH"
        assert endpoint == "/repos/owner/repo/issues/comments/202"
        assert INTEGRITY_COMMENT_MARKER in payload["body"]


def test_set_commit_status_posts_to_statuses_endpoint():
    client = GitHubClient(token="fake-token")
    expected_resp = {"state": "success", "context": "ticket-engine/integrity-gate"}

    with patch.object(client, "_request") as mock_req:
        mock_req.return_value = expected_resp

        result = client.set_commit_status(
            repo="owner/repo",
            sha="abcdef123456",
            state="success",
            description="All integrity checks passed",
            context="ticket-engine/integrity-gate",
        )

        assert result["state"] == "success"
        method, endpoint, payload = mock_req.call_args[0]
        assert method == "POST"
        assert endpoint == "/repos/owner/repo/statuses/abcdef123456"
        assert payload["state"] == "success"
        assert payload["context"] == "ticket-engine/integrity-gate"
        assert payload["description"] == "All integrity checks passed"
