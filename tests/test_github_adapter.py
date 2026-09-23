"""Contract tests for GitHub REST API adapter.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Adapters) and Ticket 06 Acceptance Criteria 1 and 7
mandate that:
1. The GitHub adapter creates a claim branch `claim/<effort>/<NN>` via the Git refs API.
2. An already-exists response (HTTP 422) means "claimed" and the ticket is skipped.
3. Adapters are tested with contract tests against recorded API responses.
4. Zero live network calls are made in tests.
"""
from __future__ import annotations

import json
import pathlib
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from ticket_engine.github import GitHubClient

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "github"


def load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_create_claim_branch_success_returns_true():
    fixture_json = load_fixture("git_refs_create_success.json")
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = client.create_claim_branch(
            repo="ilegault/ticket-engine",
            effort="phase-1",
            ticket_number=6,
            sha="75fbea6cad021b9719ba895edb1fba0952ef1e84",
        )

        assert result is True
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.method == "POST"
        assert req.full_url == "https://api.github.com/repos/ilegault/ticket-engine/git/refs"
        assert req.headers["Authorization"] == "Bearer mock_token"

        payload = json.loads(req.data.decode("utf-8"))
        assert payload["ref"] == "refs/heads/claim/phase-1/06"
        assert payload["sha"] == "75fbea6cad021b9719ba895edb1fba0952ef1e84"


def test_create_claim_branch_already_exists_returns_false():
    fixture_json = load_fixture("git_refs_create_422_already_exists.json")
    client = GitHubClient(token="mock_token")

    http_err = urllib.error.HTTPError(
        url="https://api.github.com/repos/ilegault/ticket-engine/git/refs",
        code=422,
        msg="Unprocessable Entity",
        hdrs={},
        fp=MagicMock(read=MagicMock(return_value=fixture_json.encode("utf-8"))),
    )

    with patch("urllib.request.urlopen", side_effect=http_err):
        result = client.create_claim_branch(
            repo="ilegault/ticket-engine",
            effort="phase-1",
            ticket_number=6,
            sha="75fbea6cad021b9719ba895edb1fba0952ef1e84",
        )
        assert result is False


def test_create_claim_branch_other_http_error_raises():
    client = GitHubClient(token="mock_token")

    http_err = urllib.error.HTTPError(
        url="https://api.github.com/repos/ilegault/ticket-engine/git/refs",
        code=500,
        msg="Internal Server Error",
        hdrs={},
        fp=MagicMock(read=MagicMock(return_value=b'{"message": "Server Error"}')),
    )

    with (
        patch("urllib.request.urlopen", side_effect=http_err),
        pytest.raises(urllib.error.HTTPError),
    ):
        client.create_claim_branch(
            repo="ilegault/ticket-engine",
            effort="phase-1",
            ticket_number=6,
            sha="75fbea6cad021b9719ba895edb1fba0952ef1e84",
        )
