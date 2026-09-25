"""Tests for the end-of-run dispatch report.

WHY THIS EXISTS
---------------
A run that starts nothing must say why: paused, a limit reached, or agent tickets
stuck behind `ready-for-developer` tickets. These tests build tickets through the
real parser (so statuses and blockers are read exactly as in production) and assert
on the report text a developer would read on the Actions summary page.

Faked: the GitHub and Jules clients. Real: the parser, LiveDispatcher, the CLI entry
point, and the summary file (a temp file standing in for $GITHUB_STEP_SUMMARY).
"""
from __future__ import annotations

import io
import pathlib
import urllib.error
from unittest.mock import MagicMock, patch

from ticket_engine.cli import main
from ticket_engine.config import RepoConfig
from ticket_engine.live_dispatch import LiveDispatcher
from ticket_engine.parser import Ticket, TicketParser
from ticket_engine.run_report import (
    RunFacts,
    build_run_report,
    find_duplicate_numbers,
    held_up_by,
    write_step_summary,
)

REPO = "owner/repo"


def ticket(
    number: int,
    status: str,
    blocked_by: str = "None",
    effort: str = "effort-a",
    title: str | None = None,
) -> Ticket:
    """Parse a real ticket file body, the same way the dispatcher reads the repo."""
    title = title or f"Ticket {number}"
    text = f"# {number}: {title}\n\n**Status:** {status}\n\n**Blocked by:** {blocked_by}\n"
    path = pathlib.Path(".scratch") / effort / "issues" / f"{number:02d}-t.md"
    return TicketParser().parse_text(text, filename=path.name, path=path)


def epif_chain() -> list[Ticket]:
    """The shape that stalled the Slackbot repo: one developer ticket at the root."""
    return [
        ticket(38, "done"),
        ticket(36, "ready-for-developer", title="Commit the blank EPIF template"),
        ticket(37, "ready-for-agent", "36"),
        ticket(45, "ready-for-agent", "37, 38"),
        ticket(46, "ready-for-agent", "45"),
        ticket(41, "ready-for-agent", "45, 46"),
    ]


def table_row(report: str, number: int) -> str:
    rows = [ln for ln in report.splitlines() if ln.startswith(f"| {number:02d} |")]
    assert rows, f"no table row for {number:02d} in:\n{report}"
    return rows[0]


# --- pure report -----------------------------------------------------------


def test_developer_ticket_lists_everything_it_transitively_holds_up():
    tickets = epif_chain()
    assert held_up_by(36, tickets) == [37, 41, 45, 46]

    report = build_run_report(tickets, RunFacts(repo=REPO))

    assert "Needs you (`ready-for-developer`)" in report
    row = table_row(report, 36)
    assert "Commit the blank EPIF template" in row
    assert row.endswith("| 37, 41, 45, 46 |")


def test_waiting_agent_tickets_name_the_unfinished_blocker_and_its_status():
    report = build_run_report(epif_chain(), RunFacts(repo=REPO))

    assert table_row(report, 37).endswith("| 36 (ready-for-developer) |")
    # 38 is done, so only 37 is named for 45.
    assert table_row(report, 45).endswith("| 37 (ready-for-agent) |")
    assert table_row(report, 41).endswith("| 45 (ready-for-agent), 46 (ready-for-agent) |")


def test_missing_blocker_is_named_as_missing():
    report = build_run_report([ticket(5, "ready-for-agent", "99")], RunFacts())
    assert table_row(report, 5).endswith("| 99 (no such ticket) |")


def test_started_ticket_is_listed_as_started_not_waiting():
    started = ticket(7, "ready-for-agent", title="Do the thing")
    report = build_run_report([started], RunFacts(started=[started]))

    assert "**Started 1 ticket(s):**" in report
    assert "- 07 Do the thing" in report
    assert "waiting on blockers" not in report


def test_duplicate_numbers_are_reported_with_both_files():
    a = ticket(34, "done", effort="purchase-path")
    b = ticket(34, "ready-for-developer", effort="bom-editing")

    assert find_duplicate_numbers([a, b, ticket(1, "done")]) == {34: [a, b]}
    report = build_run_report([a, b], RunFacts())
    assert "Duplicate ticket numbers" in report
    assert "`.scratch/purchase-path/issues/34-t.md`" in report
    assert "`.scratch/bom-editing/issues/34-t.md`" in report


def test_no_duplicate_section_when_numbers_are_unique():
    assert "Duplicate" not in build_run_report(epif_chain(), RunFacts())


def test_403_on_pause_variable_explains_the_missing_permission():
    report = build_run_report(
        [], RunFacts(pause_check_error="HTTP Error 403: Forbidden")
    )
    assert "Could not read `TICKET_ENGINE_PAUSED` (HTTP Error 403: Forbidden)" in report
    assert "Variables: Read and write" in report


def test_other_pause_read_errors_do_not_blame_permissions():
    report = build_run_report([], RunFacts(pause_check_error="timed out"))
    assert "Could not read `TICKET_ENGINE_PAUSED` (timed out)" in report
    assert "Variables: Read and write" not in report


def test_each_reached_limit_is_named_with_its_numbers():
    facts = RunFacts(
        jules_sessions_24h=95,
        jules_limit=100,
        jules_reserve=10,
        repo_starts_24h=3,
        daily_cap=3,
        concurrency=2,
        claims_in_flight=["claim/a/01", "claim/a/02"],
    )
    report = build_run_report([], facts)

    assert "Jules quota reserve reached: 5 session(s) left today, reserve is 10." in report
    assert "Daily cap reached: 3/3 starts in 24h." in report
    assert "Concurrency full: 2/2 tickets already claimed." in report


def test_limits_under_their_thresholds_are_not_flagged():
    facts = RunFacts(jules_sessions_24h=5, repo_starts_24h=1, daily_cap=10, concurrency=2)
    report = build_run_report([], facts)

    assert "🚦" not in report
    assert "starts in this repo (24h): 1/10" in report
    assert "claims in flight: 0/2" in report


def test_paused_repo_says_paused():
    report = build_run_report([], RunFacts(paused=True))
    assert "Repo is **paused**" in report


def test_legacy_status_is_reported_as_unreadable():
    report = build_run_report([ticket(25, "human-task")], RunFacts())
    assert "unreadable Status line" in report
    assert "25 `.scratch/effort-a/issues/25-t.md`: Unknown or legacy status 'human-task'" in report


# --- summary file ----------------------------------------------------------


def test_write_step_summary_appends_to_the_named_file(tmp_path: pathlib.Path):
    target = tmp_path / "summary.md"
    target.write_text("earlier step\n", encoding="utf-8")

    assert write_step_summary("## report\n", {"GITHUB_STEP_SUMMARY": str(target)}) is True
    assert target.read_text(encoding="utf-8") == "earlier step\n## report\n"


def test_write_step_summary_is_a_no_op_outside_actions(tmp_path: pathlib.Path):
    assert write_step_summary("## report\n", {}) is False
    assert list(tmp_path.iterdir()) == []


# --- live dispatcher records what it saw ------------------------------------


def fake_clients(pause_error: Exception | None = None) -> tuple[MagicMock, MagicMock]:
    github = MagicMock()
    github.get_default_branch_sha.return_value = "base123sha"
    github.list_claim_branches.return_value = ["claim/effort-a/09"]
    github.create_claim_branch.return_value = True
    if pause_error is not None:
        github.get_repo_variable.side_effect = pause_error
    else:
        github.get_repo_variable.return_value = None
    jules = MagicMock()
    jules.count_recent_sessions.return_value = 12
    jules.list_sessions.return_value = []
    return github, jules


def forbidden() -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.github.com/x", 403, "Forbidden", {}, io.BytesIO(b"")  # type: ignore[arg-type]
    )


def test_live_dispatch_records_pause_error_and_counts():
    github, jules = fake_clients(pause_error=forbidden())
    dispatcher = LiveDispatcher(
        repo=REPO,
        github_client=github,
        jules_client=jules,
        config=RepoConfig(concurrency=2, daily_cap=10),
        skill_text="# s",
    )

    started = dispatcher.dispatch(tickets=epif_chain())

    assert started == []
    facts = dispatcher.last_run
    assert "403" in facts.pause_check_error
    assert facts.paused is False
    assert facts.jules_sessions_24h == 12
    assert facts.repo_starts_24h == 0
    assert facts.claims_in_flight == ["claim/effort-a/09"]


def test_live_dispatch_records_why_it_stopped_early():
    github, jules = fake_clients()
    jules.count_recent_sessions.side_effect = urllib.error.URLError("no route")
    dispatcher = LiveDispatcher(
        repo=REPO, github_client=github, jules_client=jules, skill_text="# s"
    )

    assert dispatcher.dispatch(tickets=epif_chain()) == []
    assert "could not query Jules sessions" in dispatcher.last_run.stopped_early
    report = build_run_report(epif_chain(), dispatcher.last_run)
    assert "Stopped before evaluating tickets: could not query Jules sessions" in report


# --- CLI writes the report to the Actions summary ---------------------------


def test_cli_live_dispatch_writes_report_to_step_summary(
    tmp_path: pathlib.Path, monkeypatch, capsys
):
    repo_dir = tmp_path / "repo"
    issues = repo_dir / ".scratch" / "effort-a" / "issues"
    issues.mkdir(parents=True)
    (issues / "36-template.md").write_text(
        "# 36: Commit the blank EPIF template\n\n**Status:** ready-for-developer\n\n"
        "**Blocked by:** None\n",
        encoding="utf-8",
    )
    (issues / "37-fill.md").write_text(
        "# 37: Fill the EPIF\n\n**Status:** ready-for-agent\n\n**Blocked by:** 36\n",
        encoding="utf-8",
    )
    summary = tmp_path / "step_summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_REPOSITORY", REPO)
    monkeypatch.setenv("PIPELINE_TOKEN", "t")
    monkeypatch.setenv("JULES_API_KEY", "k")
    monkeypatch.delenv("TICKET_ENGINE_PAUSED", raising=False)

    github, jules = fake_clients(pause_error=forbidden())
    with (
        patch("ticket_engine.cli.GitHubClient", return_value=github),
        patch("ticket_engine.cli.JulesClient", return_value=jules),
    ):
        assert main([str(repo_dir)]) == 0

    written = summary.read_text(encoding="utf-8")
    assert "## Ticket dispatch — owner/repo" in written
    assert "**Started 0 tickets.**" in written
    assert table_row(written, 36).endswith("| 37 |")
    assert table_row(written, 37).endswith("| 36 (ready-for-developer) |")
    assert "Variables: Read and write" in written
    # The same report goes to the job log.
    assert "Needs you (`ready-for-developer`)" in capsys.readouterr().out
    github.create_claim_branch.assert_not_called()
