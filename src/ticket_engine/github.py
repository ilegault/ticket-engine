"""Thin GitHub REST API adapter.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Adapters) and Ticket 06 Acceptance Criteria 1 & 7
mandate that:
1. The GitHub adapter creates a claim branch `claim/<effort>/<NN>` via the Git refs API.
2. An already-exists response (HTTP 422) means "claimed" and the ticket is skipped.
3. This adapter isolates all GitHub REST I/O using Python standard library
   `urllib.request`. It keeps cores pure and is fully fakeable in tests.
"""
from __future__ import annotations

import base64
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
            "User-Agent": "ticket-engine",
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

    def create_claim_branch(
        self, repo: str, effort: str, ticket_number: int, sha: str
    ) -> bool:
        """Create a claim branch `claim/<effort>/<NN>` via Git refs API.

        Returns True if the branch was created successfully.
        Returns False if the branch already exists (HTTP 422), meaning claimed.
        """
        ref_name = f"refs/heads/claim/{effort}/{ticket_number:02d}"
        endpoint = f"/repos/{repo}/git/refs"
        payload = {
            "ref": ref_name,
            "sha": sha,
        }

        try:
            self._request("POST", endpoint, payload)
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 422:
                # Reference already exists -> already claimed
                logger.info("Claim branch %s already exists for repo %s", ref_name, repo)
                return False
            raise

    def get_default_branch_sha(self, repo: str, default_branch: str = "master") -> str:
        """Fetch the latest commit SHA of the default branch."""
        endpoint = f"/repos/{repo}/git/ref/heads/{default_branch}"
        data = self._request("GET", endpoint)
        if isinstance(data, dict) and "object" in data and "sha" in data["object"]:
            return str(data["object"]["sha"])
        msg = f"Failed to resolve SHA for branch '{default_branch}' on repo '{repo}'"
        raise ValueError(msg)

    def get_repo_variable(self, repo: str, name: str) -> str | None:
        """Fetch an Actions variable from the repo, returning None if not found."""
        endpoint = f"/repos/{repo}/actions/variables/{name}"
        try:
            data = self._request("GET", endpoint)
            if isinstance(data, dict):
                return data.get("value")
            return None
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    def list_claim_branches(self, repo: str) -> list[str]:
        """List active claim branch names for the repo."""
        endpoint = f"/repos/{repo}/git/matching-refs/heads/claim/"
        try:
            refs = self._request("GET", endpoint)
            if isinstance(refs, list):
                result = []
                for item in refs:
                    if isinstance(item, dict) and "ref" in item:
                        # strip refs/heads/
                        ref_str = str(item["ref"]).removeprefix("refs/heads/")
                        result.append(ref_str)
                return result
            return []
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return []
            raise

    def post_or_update_pr_comment(
        self, repo: str, pr_number: int, body: str
    ) -> dict[str, Any]:
        """Post a new comment or update existing integrity gate comment on PR."""
        marked_body = (
            f"{INTEGRITY_COMMENT_MARKER}\n{body}"
            if INTEGRITY_COMMENT_MARKER not in body
            else body
        )

        endpoint = f"/repos/{repo}/issues/{pr_number}/comments"
        comments = self._request("GET", endpoint) or []

        existing_comment_id: int | None = None
        for c in comments:
            if isinstance(c, dict) and INTEGRITY_COMMENT_MARKER in c.get("body", ""):
                existing_comment_id = c.get("id")
                break

        if existing_comment_id is not None:
            patch_endpoint = f"/repos/{repo}/issues/comments/{existing_comment_id}"
            return self._request("PATCH", patch_endpoint, {"body": marked_body})
        else:
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

    def get_file_contents(
        self, repo: str, path: str, ref: str | None = None
    ) -> dict[str, Any]:
        """Fetch content and SHA of a file in the repository."""
        endpoint = f"/repos/{repo}/contents/{path.lstrip('/')}"
        if ref:
            endpoint += f"?ref={ref}"
        data = self._request("GET", endpoint)
        if isinstance(data, dict):
            raw_content = data.get("content", "")
            encoding = data.get("encoding", "")
            if encoding == "base64" and raw_content:
                # Remove newlines before decoding
                clean_b64 = raw_content.replace("\n", "").replace("\r", "")
                decoded = base64.b64decode(clean_b64).decode("utf-8")
            else:
                decoded = raw_content
            return {
                "content": decoded,
                "sha": data.get("sha", ""),
            }
        return {"content": "", "sha": ""}

    def commit_file_change(
        self,
        repo: str,
        path: str,
        content: str,
        message: str,
        branch: str,
        sha: str | None = None,
    ) -> dict[str, Any]:
        """Commit an updated or new file to a branch via Contents API."""
        endpoint = f"/repos/{repo}/contents/{path.lstrip('/')}"
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("ascii")
        payload: dict[str, Any] = {
            "message": message,
            "content": encoded_content,
            "branch": branch,
        }
        if sha:
            payload["sha"] = sha
        return self._request("PUT", endpoint, payload)

    def convert_pr_to_draft(self, repo: str, pr_number: int) -> dict[str, Any]:
        """Convert an existing pull request to a draft PR."""
        endpoint = f"/repos/{repo}/pulls/{pr_number}"
        return self._request("PATCH", endpoint, {"draft": True})

    def merge_pull_request(
        self, repo: str, pr_number: int, merge_method: str = "merge"
    ) -> dict[str, Any] | None:
        """Merge a pull request. Returns None (logged, not raised) if GitHub
        refuses the merge, e.g. required checks not all reported yet, or a
        conflict. Callers should treat that as "leave it for a human", not a
        crash: the PR is simply left for a manual merge."""
        endpoint = f"/repos/{repo}/pulls/{pr_number}/merge"
        try:
            return self._request("PUT", endpoint, {"merge_method": merge_method})
        except urllib.error.HTTPError as exc:
            logger.warning(
                "Could not auto-merge PR #%s on %s (%s): %s",
                pr_number, repo, exc.code, exc.reason,
            )
            return None

    def add_issue_labels(
        self, repo: str, issue_number: int, labels: list[str]
    ) -> list[str]:
        """Add labels to an issue or pull request."""
        endpoint = f"/repos/{repo}/issues/{issue_number}/labels"
        res = self._request("POST", endpoint, {"labels": labels})
        if isinstance(res, list):
            return [str(item.get("name", "")) for item in res if isinstance(item, dict)]
        return labels

    def delete_branch(self, repo: str, branch: str) -> bool:
        """Delete a branch/ref via Git refs API."""
        if branch.startswith("refs/"):
            ref_path = branch.removeprefix("refs/")
        elif branch.startswith(("heads/", "claim/")):
            ref_path = branch if branch.startswith("heads/") else f"heads/{branch}"
        else:
            ref_path = f"heads/{branch}"

        endpoint = f"/repos/{repo}/git/refs/{ref_path}"
        try:
            self._request("DELETE", endpoint)
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise

    def set_repo_variable(self, repo: str, name: str, value: str) -> bool:
        """Set or update an Actions repository variable."""
        patch_endpoint = f"/repos/{repo}/actions/variables/{name}"
        try:
            self._request("PATCH", patch_endpoint, {"name": name, "value": value})
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                post_endpoint = f"/repos/{repo}/actions/variables"
                self._request("POST", post_endpoint, {"name": name, "value": value})
                return True
            raise

