"""Thin Jules API adapter.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Jules API) and Ticket 06 Acceptance Criteria 2 & 3
mandate that:
1. Sessions are created with `prompt`, `sourceContext` (source plus
   `githubRepoContext.startingBranch`), `title` (`<effort>-<NN>: <ticket title>`),
   `automationMode: AUTO_CREATE_PR`, and plan approval not required.
2. Auth is via the `X-Goog-Api-Key` header.
3. The adapter lists sessions and counts those created in the rolling 24 hours.
4. Privacy invariant (ADR 0002): Prompts and API keys are NEVER logged or printed.
5. ADR 0004: the dispatcher answers a session that stops to ask a question. It reads
   the session's activities (`list_activities`) to count its own earlier replies and
   replies with `send_message` (`POST /v1alpha/sessions/<id>:sendMessage`,
   body `{"prompt": ...}`, empty response).
"""
from __future__ import annotations

import datetime
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)


def _parse_iso_timestamp(ts_str: str) -> datetime.datetime:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime."""
    clean = ts_str.rstrip("Z")
    dt = datetime.datetime.fromisoformat(clean)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt


class JulesClient:
    """Client for interacting with Google's Jules API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://jules.googleapis.com/v1alpha",
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")

    def _request(
        self, method: str, endpoint: str, payload: dict[str, Any] | None = None
    ) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "X-Goog-Api-Key": self.api_key,
            "User-Agent": "ticket-engine",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req) as resp:
                resp_bytes = resp.read()
                if not resp_bytes:
                    return None
                return json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            # Do NOT log payload, prompt, or sensitive error responses
            logger.error("Jules API HTTPError %s %s: %s", exc.code, exc.reason, err_body)
            raise

    def create_session(
        self,
        prompt: str,
        repo: str,
        starting_branch: str,
        title: str,
        require_plan_approval: bool = False,
        automation_mode: str = "AUTO_CREATE_PR",
    ) -> dict[str, Any]:
        """Create a new Jules cloud worker session."""
        source = repo if repo.startswith("sources/") else f"sources/github/{repo}"
        payload = {
            "prompt": prompt,
            "sourceContext": {
                "source": source,
                "githubRepoContext": {
                    "startingBranch": starting_branch,
                },
            },
            "title": title,
            "automationMode": automation_mode,
            "requirePlanApproval": require_plan_approval,
        }

        endpoint = "/sessions"
        data = self._request("POST", endpoint, payload)
        if isinstance(data, dict):
            return data
        return {}

    @staticmethod
    def _session_path(session_name: str) -> str:
        name = session_name.strip().strip("/")
        return name if name.startswith("sessions/") else f"sessions/{name}"

    def send_message(self, session_name: str, message: str) -> None:
        """Send a user message to a session (ADR 0004). Never logs the message."""
        self._request(
            "POST", f"/{self._session_path(session_name)}:sendMessage", {"prompt": message}
        )

    def list_activities(self, session_name: str, page_size: int = 100) -> list[dict[str, Any]]:
        """Every activity of a session, following `nextPageToken`."""
        base = f"/{self._session_path(session_name)}/activities?pageSize={page_size}"
        out: list[dict[str, Any]] = []
        token = ""
        while True:
            endpoint = base + (f"&pageToken={urllib.parse.quote(token)}" if token else "")
            data = self._request("GET", endpoint)
            if not isinstance(data, dict):
                break
            out.extend(a for a in data.get("activities", []) if isinstance(a, dict))
            token = str(data.get("nextPageToken") or "")
            if not token:
                break
        return out

    def list_sessions(self) -> list[dict[str, Any]]:
        """List Jules sessions."""
        endpoint = "/sessions"
        data = self._request("GET", endpoint)
        if isinstance(data, dict) and "sessions" in data:
            return list(data["sessions"])
        return []

    def count_recent_sessions(
        self,
        hours: int = 24,
        now: datetime.datetime | None = None,
    ) -> int:
        """Count sessions created within the last `hours` (default: 24)."""
        ref_time = now or datetime.datetime.now(datetime.UTC)
        cutoff = ref_time - datetime.timedelta(hours=hours)

        sessions = self.list_sessions()
        count = 0
        for sess in sessions:
            create_time_str = sess.get("createTime")
            if not create_time_str:
                continue
            try:
                dt = _parse_iso_timestamp(create_time_str)
                if dt >= cutoff:
                    count += 1
            except (ValueError, TypeError) as exc:
                logger.warning("Failed to parse createTime '%s': %s", create_time_str, exc)
                continue

        return count
