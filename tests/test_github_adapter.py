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


def _resp(body: dict | None) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(body).encode("utf-8") if body is not None else b""
    mock_resp.__enter__.return_value = mock_resp
    return mock_resp


def test_enable_auto_merge_looks_up_node_id_and_calls_graphql():
    client = GitHubClient(token="mock_token")
    responses = [
        _resp({"number": 7, "node_id": "PR_kwDOnode7"}),
        _resp({"data": {"enablePullRequestAutoMerge": {"pullRequest": {"number": 7}}}}),
    ]

    with patch("urllib.request.urlopen", side_effect=responses) as mock_urlopen:
        ok = client.enable_auto_merge(repo="owner/repo", pr_number=7, merge_method="squash")

    assert ok is True
    get_req = mock_urlopen.call_args_list[0][0][0]
    assert get_req.method == "GET"
    assert get_req.full_url == "https://api.github.com/repos/owner/repo/pulls/7"
    gql_req = mock_urlopen.call_args_list[1][0][0]
    assert gql_req.method == "POST"
    assert gql_req.full_url == "https://api.github.com/graphql"
    body = json.loads(gql_req.data.decode("utf-8"))
    assert "enablePullRequestAutoMerge" in body["query"]
    assert body["variables"] == {"id": "PR_kwDOnode7", "method": "SQUASH"}


def test_enable_auto_merge_returns_false_when_github_refuses():
    client = GitHubClient(token="mock_token")
    responses = [
        _resp({"number": 7, "node_id": "PR_x"}),
        _resp({"data": None, "errors": [{"message": "Pull request is in clean status"}]}),
    ]

    with patch("urllib.request.urlopen", side_effect=responses):
        assert client.enable_auto_merge(repo="owner/repo", pr_number=7) is False


def test_enable_auto_merge_rejects_unknown_merge_method():
    client = GitHubClient(token="mock_token")
    with pytest.raises(ValueError):
        client.enable_auto_merge(repo="owner/repo", pr_number=7, merge_method="octopus")


def test_disable_auto_merge_calls_graphql_and_tolerates_nothing_to_disable():
    client = GitHubClient(token="mock_token")
    responses = [
        _resp({"number": 7, "node_id": "PR_x"}),
        _resp({"data": None, "errors": [{"message": "auto merge is not enabled"}]}),
    ]

    with patch("urllib.request.urlopen", side_effect=responses) as mock_urlopen:
        assert client.disable_auto_merge(repo="owner/repo", pr_number=7) is False

    body = json.loads(mock_urlopen.call_args_list[1][0][0].data.decode("utf-8"))
    assert "disablePullRequestAutoMerge" in body["query"]
    assert body["variables"] == {"id": "PR_x"}
