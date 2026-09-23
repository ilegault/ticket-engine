"""Contract tests for Jules API adapter.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Jules API) and Ticket 06 Acceptance Criteria 2, 3, 7
mandate that:
1. The Jules adapter creates a session with prompt, sourceContext, title,
   automationMode: AUTO_CREATE_PR, and no plan approval required.
2. Auth is via the X-Goog-Api-Key header.
3. The adapter lists sessions and can count those created in the rolling 24 hours.
4. Contract tests run against recorded API responses.
5. Zero live network calls are made in tests.
"""
from __future__ import annotations

import datetime
import json
import pathlib
from unittest.mock import MagicMock, patch

from ticket_engine.jules import JulesClient

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "jules"


def load_fixture(name: str) -> str:
    return (_FIXTURES_DIR / name).read_text(encoding="utf-8")


def test_create_session_payload_and_auth_header():
    fixture_json = load_fixture("create_session_success.json")
    client = JulesClient(api_key="mock_jules_key")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        resp_data = client.create_session(
            prompt="Implement ticket 06",
            repo="ilegault/ticket-engine",
            starting_branch="master",
            title="phase-1-06: Live dispatch through Jules",
            automation_mode="AUTO_CREATE_PR",
            require_plan_approval=False,
        )

        assert resp_data["id"] == "sessions/123456789"
        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]

        assert req.method == "POST"
        assert req.full_url == "https://jules.googleapis.com/v1alpha/sessions"
        assert req.headers["X-goog-api-key"] == "mock_jules_key"

        payload = json.loads(req.data.decode("utf-8"))
        assert payload["prompt"] == "Implement ticket 06"
        assert payload["title"] == "phase-1-06: Live dispatch through Jules"
        assert payload["automationMode"] == "AUTO_CREATE_PR"
        assert payload["requirePlanApproval"] is False
        assert payload["sourceContext"]["source"] == "sources/github/ilegault/ticket-engine"
        assert payload["sourceContext"]["githubRepoContext"]["startingBranch"] == "master"


def test_list_sessions_parses_recorded_response():
    fixture_json = load_fixture("list_sessions_success.json")
    client = JulesClient(api_key="mock_jules_key")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        sessions = client.list_sessions()
        assert len(sessions) == 3
        assert sessions[0]["id"] == "sessions/1001"
        assert sessions[1]["id"] == "sessions/1002"
        assert sessions[2]["id"] == "sessions/1003"


def test_count_recent_sessions_with_reference_time():
    fixture_json = load_fixture("list_sessions_success.json")
    client = JulesClient(api_key="mock_jules_key")

    mock_resp = MagicMock()
    mock_resp.read.return_value = fixture_json.encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    with patch("urllib.request.urlopen", return_value=mock_resp):
        # Reference time: 2026-09-22T21:00:00Z
        now = datetime.datetime(2026, 9, 22, 21, 0, 0, tzinfo=datetime.UTC)
        # In fixture:
        # 1001: 2026-09-22T12:00:00Z (9h ago -> within 24h)
        # 1002: 2026-09-22T14:30:00Z (6.5h ago -> within 24h)
        # 1003: 2026-09-22T20:00:00Z (1h ago -> within 24h)
        count_24h = client.count_recent_sessions(hours=24, now=now)
        assert count_24h == 3

        # Within 2 hours: only 1003 is within 2 hours
        count_2h = client.count_recent_sessions(hours=2, now=now)
        assert count_2h == 1
