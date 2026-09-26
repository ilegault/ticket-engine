"""Scheduled check that alerts the developer when the box goes silent.

WHY THIS EXISTS
---------------
Ticket 33 (§Held changes), ADR 0007 rule 4: the box posts only fixed-template
text, and the engine must notice, on its own schedule, when the box has
stopped checking in. This script reads the `engine:box-status` issue, parses
it with `box_status.parse_box_status`, and decides via the pure
`box_status.silent_check_action` whether to open or close the
"Box alert: box silent" issue (labelled `engine:box-alert`). The workflow
(`.github/workflows/box-silent-check.yml`) only calls `main`; every decision
lives in the pure function, not here.

This ticket changes `.github/`, so the integrity gate always holds its PR
(ADR 0001 check 4) and it lands only by hand.
"""
from __future__ import annotations

import datetime
import os
import sys

from ticket_engine.box_status import (
    AlertKind,
    parse_box_status,
    render_box_alert,
    silent_check_action,
)
from ticket_engine.config import RepoConfig
from ticket_engine.github import GitHubClient

_LABEL_BOX_STATUS = "engine:box-status"
_LABEL_BOX_ALERT = "engine:box-alert"
_ALERT_TITLE = "Box alert: box silent"


def main(client, repo: str, now: datetime.datetime) -> int:
    """Check the box status issue and open or close the box-silent alert.

    `client` is a GitHub client offering `list_open_issues`, `find_open_issue`,
    `create_issue` and `close_issue` (the shape of `github.GitHubClient`),
    injected so tests can fake it. Prints exactly one status line and returns 0.
    """
    status = None
    for issue in client.list_open_issues(repo, _LABEL_BOX_STATUS):
        if isinstance(issue, dict):
            status = parse_box_status(str(issue.get("body") or ""))
            break

    alert_number = client.find_open_issue(repo, _LABEL_BOX_ALERT, _ALERT_TITLE)
    alert_open = alert_number is not None

    silent_hours = RepoConfig().box_silent_hours
    action = silent_check_action(status, now, silent_hours, alert_open)

    if action == "open":
        owner = repo.split("/")[0]
        since = status.checked_in_at if status is not None else now
        title, body = render_box_alert(AlertKind.box_silent, owner, since)
        client.create_issue(repo, title, body, [_LABEL_BOX_ALERT])
        print("box silent alert opened")
    elif action == "close":
        client.close_issue(repo, alert_number)
        print("box silent alert closed")
    else:
        print("no change")
    return 0


if __name__ == "__main__":
    _token = os.environ["GITHUB_TOKEN"]
    _repo = os.environ["GITHUB_REPOSITORY"]
    _client = GitHubClient(_token)
    sys.exit(main(_client, _repo, datetime.datetime.now(datetime.UTC)))
