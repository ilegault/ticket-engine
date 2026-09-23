"""Thin GitHub REST API adapter.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Adapters) and Ticket 03 Acceptance Criterion 5
require posting the integrity verdict and reasons to the target repo's PR as an
updated (not duplicated) comment and updating the commit check status.
This thin adapter isolates all GitHub REST I/O using Python standard library
`urllib.request`. It keeps cores pure and is fully fakeable in tests.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger(__name__)

INTEGRITY_COMMENT_MARKER = "<!-- ticket-engine-integrity-gate -->"


class GitHubClient:
    """Client for interacting with GitHub REST API."""

    def __init__(self, token: str, base_url: str = "https://api.github.com"):
        self.token = token
        self.base_url = base_url.rstrip("/")

    def _request(
        self, method: str, endpoint: str, payload: dict[str, Any] | None = None
    ) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "User-Agent": "ticket-engine-integrity-gate",
            "X-GitHub-Api-Version": "2022-11-28",
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
            logger.error("GitHub API HTTPError %s %s: %s", exc.code, exc.reason, err_body)
            raise

    def post_or_update_pr_comment(
        self, repo: str, pr_number: int, body: str
    ) -> dict[str, Any]:
        """Post a new comment or update existing integrity gate comment on PR."""
        marked_body = f"{INTEGRITY_COMMENT_MARKER}\n{body}" if INTEGRITY_COMMENT_MARKER not in body else body

        # Fetch existing comments
        endpoint = f"/repos/{repo}/issues/{pr_number}/comments"
        comments = self._request("GET", endpoint) or []

        existing_comment_id: int | None = None
        for c in comments:
            if isinstance(c, dict) and INTEGRITY_COMMENT_MARKER in c.get("body", ""):
                existing_comment_id = c.get("id")
                break

        if existing_comment_id is not None:
            # Update comment
            patch_endpoint = f"/repos/{repo}/issues/comments/{existing_comment_id}"
            return self._request("PATCH", patch_endpoint, {"body": marked_body})
        else:
            # Create comment
            return self._request("POST", endpoint, {"body": marked_body})

    def set_commit_status(
        self,
        repo: str,
        sha: str,
        state: str,
        description: str,
        context: str = "ticket-engine/integrity-gate",
    ) -> dict[str, Any]:
        """Set commit status on a specific commit SHA."""
        endpoint = f"/repos/{repo}/statuses/{sha}"
        payload = {
            "state": state,
            "description": description[:140],
            "context": context,
        }
        return self._request("POST", endpoint, payload)
