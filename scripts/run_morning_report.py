"""Morning report runner — gathers live data and updates the pinned engine issue.

WHY THIS EXISTS
---------------
Ticket 08 AC: the morning-report workflow needs a script that gathers per-repo
data from GitHub and the Jules API, renders the report via the pure
render_morning_report() function, then creates or rewrites the single pinned
issue in the engine repo.

ADR 0002 (privacy): this script never logs tokens, prompts, or denylist entries.
The rendered report itself is ADR 0002-compliant: the renderer emits only PR
numbers, ticket titles, repo names, and quota counts.
"""
from __future__ import annotations

import datetime
import logging
import os
import sys
import tomllib
import urllib.error
import urllib.request

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

_REPORT_ISSUE_TITLE = "Morning Report"
_REPORT_HOURS = 24  # how far back to look for merged PRs
_LABEL_ESCALATED = "engine:escalated"
_LABEL_HOLD = "engine:hold"


def _gh_request(
    path: str,
    token: str,
    method: str = "GET",
    body: bytes | None = None,
) -> dict | list:
    url = f"https://api.github.com{path}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        import json

        return json.loads(resp.read())


def _find_or_create_issue(engine_repo: str, token: str, body: str) -> int:
    """Return the issue number of the pinned morning-report issue, creating it if absent."""
    import json

    # Search for existing open issue
    path = f"/repos/{engine_repo}/issues?state=open&labels=engine:morning-report&per_page=10"
    try:
        issues = _gh_request(path, token)
        if isinstance(issues, list):
            for issue in issues:
                if isinstance(issue, dict) and issue.get("title") == _REPORT_ISSUE_TITLE:
                    return int(issue["number"])
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError, KeyError) as exc:
        logger.warning("Could not search for existing morning-report issue: %s", exc)

    # Create new issue
    payload = json.dumps(
        {
            "title": _REPORT_ISSUE_TITLE,
            "body": body,
            "labels": ["engine:morning-report"],
        }
    ).encode()
    result = _gh_request(f"/repos/{engine_repo}/issues", token, method="POST", body=payload)
    if isinstance(result, dict):
        return int(result["number"])
    msg = "Failed to create morning-report issue"
    raise RuntimeError(msg)


def _update_issue(engine_repo: str, token: str, issue_number: int, body: str) -> None:
    import json

    payload = json.dumps({"body": body}).encode()
    _gh_request(
        f"/repos/{engine_repo}/issues/{issue_number}",
        token,
        method="PATCH",
        body=payload,
    )


def _fetch_merged_prs(repo: str, token: str, since_hours: int) -> list[dict]:
    cutoff = datetime.datetime.now(datetime.UTC) - datetime.timedelta(hours=since_hours)
    path = (
        f"/repos/{repo}/pulls"
        f"?state=closed&sort=updated&direction=desc&per_page=50"
    )
    try:
        prs = _gh_request(path, token)
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        return []

    merged: list[dict] = []
    if not isinstance(prs, list):
        return merged
    for pr in prs:
        if not isinstance(pr, dict):
            continue
        merged_at_str = pr.get("merged_at")
        if not merged_at_str:
            continue
        try:
            merged_at = datetime.datetime.fromisoformat(merged_at_str)
        except ValueError:
            continue
        if merged_at >= cutoff:
            merged.append(pr)
    return merged


def _fetch_open_prs_with_label(repo: str, token: str, label: str) -> list[dict]:
    path = f"/repos/{repo}/issues?state=open&labels={label}&per_page=50"
    try:
        result = _gh_request(path, token)
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        return []
    return [r for r in result if isinstance(r, dict) and r.get("pull_request")] if isinstance(result, list) else []


def _fetch_paused(repo: str, token: str) -> bool:
    path = f"/repos/{repo}/actions/variables/TICKET_ENGINE_PAUSED"
    try:
        result = _gh_request(path, token)
        if isinstance(result, dict):
            return str(result.get("value", "")).strip().lower() in ("1", "true", "yes")
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        pass
    return False


def _fetch_tickets(repo: str, token: str) -> list:
    """Fetch parsed tickets from .scratch/*/issues/ in the target repo."""
    from ticket_engine.parser import TicketParser

    path = f"/repos/{repo}/git/trees/HEAD?recursive=1"
    parser = TicketParser()
    tickets = []
    try:
        tree = _gh_request(path, token)
        if not isinstance(tree, dict):
            return tickets
        for item in tree.get("tree", []):
            if not isinstance(item, dict):
                continue
            p = item.get("path", "")
            import re

            if not re.match(r"\.scratch/.+/issues/\d+-.*\.md$", p):
                continue
            # Fetch file content
            contents_path = f"/repos/{repo}/contents/{p}"
            try:
                file_data = _gh_request(contents_path, token)
                if isinstance(file_data, dict):
                    import base64

                    content = base64.b64decode(file_data.get("content", "")).decode("utf-8", errors="replace")
                    ticket = parser.parse_text(content, filename=p.split("/")[-1])
                    tickets.append(ticket)
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
                continue
    except (urllib.error.HTTPError, urllib.error.URLError, ValueError):
        pass
    return tickets


def _count_jules_sessions(api_key: str) -> tuple[int, int]:
    """Return (sessions_24h, limit=100) by querying the Jules API."""
    try:
        from ticket_engine.jules import JulesClient

        client = JulesClient(api_key=api_key)
        count = client.count_recent_sessions(hours=24)
        return count, 100
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not query Jules quota: %s", exc)
        return 0, 100


def main() -> int:
    token = os.environ.get("PIPELINE_TOKEN", "")
    jules_key = os.environ.get("JULES_API_KEY", "")
    engine_repo = os.environ.get("ENGINE_REPO", "")

    if not token:
        logger.error("PIPELINE_TOKEN not set")
        return 1
    if not engine_repo:
        logger.error("ENGINE_REPO not set")
        return 1

    # Load target repo list
    import pathlib

    repos_config = pathlib.Path(__file__).resolve().parent.parent / "engine-repos.toml"
    if not repos_config.is_file():
        logger.error("engine-repos.toml not found at %s", repos_config)
        return 1

    with repos_config.open("rb") as f:
        repos_data = tomllib.load(f)
    target_repos: list[str] = repos_data.get("repos", [])

    if not target_repos:
        logger.warning("No repos listed in engine-repos.toml; report will be empty.")

    from ticket_engine.dispatch import MergedPR, OpenPR, WorldSnapshot
    from ticket_engine.morning_report import MorningReportData, render_morning_report

    now = datetime.datetime.now(datetime.UTC)
    repo_snapshots: list[WorldSnapshot] = []

    for repo in target_repos:
        logger.info("Gathering data for %s", repo)

        merged_raw = _fetch_merged_prs(repo, token, since_hours=_REPORT_HOURS)
        merged_prs = [
            MergedPR(
                number=int(pr["number"]),
                title=str(pr.get("title", "")),
                ticket_number=0,
            )
            for pr in merged_raw
        ]

        escalated_raw = _fetch_open_prs_with_label(repo, token, _LABEL_ESCALATED)
        held_raw = _fetch_open_prs_with_label(repo, token, _LABEL_HOLD)

        def _to_open_pr(raw: dict, label: str) -> OpenPR:
            return OpenPR(
                number=int(raw["number"]),
                branch=str(raw.get("head", {}).get("ref", "") if "head" in raw else ""),
                ticket_number=0,
                labels=(label,),
            )

        open_prs_list = [_to_open_pr(r, _LABEL_ESCALATED) for r in escalated_raw]
        open_prs_list += [_to_open_pr(r, _LABEL_HOLD) for r in held_raw]

        tickets = _fetch_tickets(repo, token)
        paused = _fetch_paused(repo, token)

        snapshot = WorldSnapshot(
            repo_name=repo,
            tickets=tickets,
            open_prs=open_prs_list,
            merged_prs=merged_prs,
            paused=paused,
            now=now,
        )
        repo_snapshots.append(snapshot)

    jules_24h, jules_limit = (0, 100)
    if jules_key:
        jules_24h, jules_limit = _count_jules_sessions(jules_key)

    report_data = MorningReportData(
        repo_snapshots=repo_snapshots,
        jules_sessions_24h=jules_24h,
        jules_limit=jules_limit,
        now=now,
    )
    report_text = render_morning_report(report_data)

    issue_number = _find_or_create_issue(engine_repo, token, report_text)
    _update_issue(engine_repo, token, issue_number, report_text)
    logger.info("Morning report updated: issue #%d", issue_number)
    return 0


if __name__ == "__main__":
    sys.exit(main())
