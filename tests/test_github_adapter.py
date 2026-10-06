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

import datetime
import io
import json
import logging
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


def test_get_branch_head_time_success():
    fixture_json = load_fixture("branch_get_success.json")
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        result = client.get_branch_head_time(
            repo="owner/repo",
            branch="ticket/phase-1-01-feature",
        )

        assert result == datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.UTC)
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.method == "GET"
        assert req.full_url == "https://api.github.com/repos/owner/repo/branches/ticket/phase-1-01-feature"


def test_get_branch_head_time_404_returns_none():
    client = GitHubClient(token="mock_token")

    err = urllib.error.HTTPError(
        url="https://api.github.com/repos/owner/repo/branches/missing",
        code=404,
        msg="Not Found",
        hdrs={},
        fp=io.BytesIO(b'{"message": "Branch not found"}') if hasattr(io, "BytesIO") else None,
    )

    with patch("urllib.request.urlopen", side_effect=err):
        result = client.get_branch_head_time(repo="owner/repo", branch="missing")
        assert result is None


# --- escalation issues (ticket 26) -------------------------------------------


def test_create_issue_posts_title_body_labels_and_returns_number():
    fixture_json = load_fixture("issue_create_success.json")
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        number = client.create_issue(
            repo="owner/repo",
            title="Escalation: phase-1-07 test-ticket-7",
            body="@owner\nTicket: owner/repo #07\n",
            labels=["escalation"],
        )

    assert number == 101
    req = mock_urlopen.call_args[0][0]
    assert req.method == "POST"
    assert req.full_url == "https://api.github.com/repos/owner/repo/issues"
    payload = json.loads(req.data.decode("utf-8"))
    assert payload == {
        "title": "Escalation: phase-1-07 test-ticket-7",
        "body": "@owner\nTicket: owner/repo #07\n",
        "labels": ["escalation"],
    }


def test_find_open_issue_matches_exact_title():
    fixture_json = load_fixture("issues_open_escalation_list.json")
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        number = client.find_open_issue(
            repo="owner/repo", label="escalation", title="Escalation: phase-1-07 test-ticket-7"
        )

    assert number == 101
    req = mock_urlopen.call_args[0][0]
    assert req.method == "GET"
    assert req.full_url == (
        "https://api.github.com/repos/owner/repo/issues?state=open&labels=escalation"
    )


def test_find_open_issue_returns_none_when_no_exact_title_match():
    fixture_json = load_fixture("issues_open_escalation_list.json")
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        number = client.find_open_issue(
            repo="owner/repo", label="escalation", title="Escalation: phase-1-99 nope"
        )

    assert number is None


def test_close_issue_sends_patch_state_closed():
    fixture_json = load_fixture("issue_close_success.json")
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        res = client.close_issue(repo="owner/repo", number=101)

    assert res["state"] == "closed"
    req = mock_urlopen.call_args[0][0]
    assert req.method == "PATCH"
    assert req.full_url == "https://api.github.com/repos/owner/repo/issues/101"
    assert json.loads(req.data.decode("utf-8")) == {"state": "closed"}


# --- pull requests (ticket 29) -----------------------------------------------


def test_create_pull_request_posts_and_returns_number():
    fixture_json = load_fixture("pull_create_success.json")
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        number = client.create_pull_request(
            repo="owner/repo",
            head="ticket/phase-1-09-add-feature",
            base="master",
            title="phase-1-09: Add feature",
            body="Ticket 09 worked by the box.\n\nTicket file: .scratch/phase-1/issues/09-add-feature.md\nBranch: ticket/phase-1-09-add-feature",
        )

    assert number == 42
    req = mock_urlopen.call_args[0][0]
    assert req.method == "POST"
    assert req.full_url == "https://api.github.com/repos/owner/repo/pulls"
    payload = json.loads(req.data.decode("utf-8"))
    assert payload == {
        "title": "phase-1-09: Add feature",
        "head": "ticket/phase-1-09-add-feature",
        "base": "master",
        "body": "Ticket 09 worked by the box.\n\nTicket file: .scratch/phase-1/issues/09-add-feature.md\nBranch: ticket/phase-1-09-add-feature",
        "draft": False,
    }


def test_find_open_pr_returns_number_when_open_pr_exists():
    fixture_json = load_fixture("pulls_open_list.json")
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        number = client.find_open_pr(repo="owner/repo", head_branch="ticket/phase-1-09-add-feature")

    assert number == 43
    req = mock_urlopen.call_args[0][0]
    assert req.method == "GET"
    assert req.full_url == (
        "https://api.github.com/repos/owner/repo/pulls?state=open&head=owner:ticket/phase-1-09-add-feature"
    )


def test_find_open_pr_returns_none_when_no_open_pr():
    fixture_json = load_fixture("pulls_open_list_empty.json")
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        number = client.find_open_pr(repo="owner/repo", head_branch="ticket/phase-1-09-add-feature")

    assert number is None


def test_list_check_runs_returns_name_conclusion_pairs():
    """Ticket 30 AC1: list_check_runs returns (name, conclusion) pairs from the
    recorded GET /repos/{repo}/commits/{ref}/check-runs response."""
    fixture_json = load_fixture("check_runs_mixed.json")
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        runs = client.list_check_runs(repo="owner/repo", ref="ticket/phase-1-30-fix-ci")

    assert runs == [("integrity-gate", "failure"), ("pytest", "success")]
    req = mock_urlopen.call_args[0][0]
    assert req.method == "GET"
    assert req.full_url == (
        "https://api.github.com/repos/owner/repo/commits/ticket/phase-1-30-fix-ci/check-runs"
    )


def test_list_open_issues_returns_the_recorded_list():
    fixture_json = load_fixture("issues_open_escalation_list.json")
    client = GitHubClient(token="mock_token")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        issues = client.list_open_issues(repo="owner/repo", label="escalation")

    assert [i["number"] for i in issues] == [101, 102]
    req = mock_urlopen.call_args[0][0]
    assert req.method == "GET"
    assert req.full_url == (
        "https://api.github.com/repos/owner/repo/issues?state=open&labels=escalation"
    )



# --- Ticket 49: expected GitHub answers are not logged as errors -------------

_PAUSE_404_BODY = (
    '{"message":"Not Found","documentation_url":"https://docs.github.com/rest/'
    'actions/variables#get-a-repository-variable","status":"404"}'
)


def _http_error(code: int, reason: str, body: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.github.com/x",
        code=code,
        msg=reason,
        hdrs={},
        fp=MagicMock(read=MagicMock(return_value=body.encode("utf-8"))),
    )


def _loud_records(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_get_repo_variable_missing_returns_none_without_an_error_log(caplog):
    caplog.set_level(logging.DEBUG, logger="ticket_engine.github")
    client = GitHubClient(token="mock_token")
    err = _http_error(404, "Not Found", _PAUSE_404_BODY)
    with patch("urllib.request.urlopen", side_effect=err):
        assert client.get_repo_variable("o/r", "TICKET_ENGINE_PAUSED") is None
    assert _loud_records(caplog) == []


def test_create_claim_branch_already_exists_logs_no_error(caplog):
    caplog.set_level(logging.DEBUG, logger="ticket_engine.github")
    client = GitHubClient(token="mock_token")
    err = _http_error(422, "Unprocessable Entity", '{"message":"Reference already exists"}')
    with patch("urllib.request.urlopen", side_effect=err):
        assert client.create_claim_branch("o/r", "e", 1, "abc") is False
    assert _loud_records(caplog) == []


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda c: c.get_branch_head_time("o/r", "b"), None),
        (lambda c: c.list_claim_branches("o/r"), []),
        (lambda c: c.remove_issue_label("o/r", 1, "l"), False),
        (lambda c: c.delete_branch("o/r", "b"), False),
        (lambda c: c.list_issues("o/r"), []),
        (lambda c: c.find_open_pr("o/r", "b"), None),
    ],
)
def test_expected_404s_return_their_missing_value_without_an_error_log(
    caplog, call, expected
):
    caplog.set_level(logging.DEBUG, logger="ticket_engine.github")
    client = GitHubClient(token="mock_token")
    err = _http_error(404, "Not Found", '{"message":"Not Found"}')
    with patch("urllib.request.urlopen", side_effect=err):
        assert call(client) == expected
    assert _loud_records(caplog) == []


def test_unexpected_status_still_logs_error_with_body(caplog):
    caplog.set_level(logging.DEBUG, logger="ticket_engine.github")
    client = GitHubClient(token="mock_token")
    err = _http_error(
        403, "Forbidden", '{"message":"Resource not accessible by personal access token"}'
    )
    with patch("urllib.request.urlopen", side_effect=err), pytest.raises(urllib.error.HTTPError):
        client.get_repo_variable("o/r", "TICKET_ENGINE_PAUSED")
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    msg = errors[0].getMessage()
    assert "403" in msg
    assert "Resource not accessible by personal access token" in msg
