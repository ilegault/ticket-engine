"""Tests for the morning-report pure renderer.

WHY THIS EXISTS
---------------
Ticket 08 AC: "Rendering is a pure function of the same world snapshot the
dispatcher uses; tests assert on the rendered text for sample snapshots."
Each test below drives render_morning_report() from outside with a
WorldSnapshot and asserts on the returned string — no I/O, no mocking.
"""
from __future__ import annotations

import datetime
import importlib.util
import pathlib
import urllib.error

from ticket_engine.box_status import BoxState, BoxStatus, render_box_status
from ticket_engine.config import RepoConfig
from ticket_engine.dispatch import (
    DispatchCore,
    MergedPR,
    OpenPR,
    WorldSnapshot,
    classify_box,
)
from ticket_engine.morning_report import MorningReportData, render_morning_report
from ticket_engine.parser import ParseFinding, Ticket, TicketParser
from ticket_engine.run_report import RunFacts, build_run_report

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime.datetime(2026, 9, 22, 12, 0, 0, tzinfo=datetime.UTC)


def _ticket(
    number: int,
    status: str = "ready-for-agent",
    runner: str = "any",
    blocked_by: list[int] | None = None,
    findings: list[ParseFinding] | None = None,
) -> Ticket:
    return Ticket(
        number=number,
        title=f"Ticket {number}",
        slug=f"ticket-{number}",
        status=status,
        runner=runner,
        blocked_by=blocked_by or [],
        findings=findings or [],
    )


def _parsed_ticket(
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


def _table_row(report: str, number: int) -> str:
    rows = [ln for ln in report.splitlines() if ln.startswith(f"| {number:02d} |")]
    assert rows, f"no table row for {number:02d} in:\n{report}"
    return rows[0]


def _open_pr(number: int, ticket_number: int, labels: tuple[str, ...] = ()) -> OpenPR:
    return OpenPR(
        number=number,
        branch=f"ticket/phase-1-{ticket_number:02d}-something",
        ticket_number=ticket_number,
        labels=labels,
    )


def _data(
    snapshots: list[WorldSnapshot],
    jules_sessions_24h: int = 0,
    jules_limit: int = 100,
) -> MorningReportData:
    return MorningReportData(
        repo_snapshots=snapshots,
        jules_sessions_24h=jules_sessions_24h,
        jules_limit=jules_limit,
        now=_NOW,
    )


# ---------------------------------------------------------------------------
# AC 1: merged PRs appear with links
# ---------------------------------------------------------------------------


def test_render_morning_report_shows_merged_prs():
    snapshot = WorldSnapshot(
        repo_name="owner/slackbot",
        merged_prs=[
            MergedPR(number=12, title="Fix auth", ticket_number=5),
            MergedPR(number=13, title="Add rate limiting", ticket_number=6),
        ],
    )
    report = render_morning_report(_data([snapshot]))
    assert "owner/slackbot" in report
    assert "Fix auth" in report
    assert "Add rate limiting" in report
    assert "#12" in report
    assert "#13" in report


def test_render_morning_report_merged_pr_link_contains_repo_and_number():
    snapshot = WorldSnapshot(
        repo_name="owner/myrepo",
        merged_prs=[MergedPR(number=7, title="Some work", ticket_number=3)],
    )
    report = render_morning_report(_data([snapshot]))
    assert "https://github.com/owner/myrepo/pull/7" in report


# ---------------------------------------------------------------------------
# AC 2: escalated PRs appear with links to briefs
# ---------------------------------------------------------------------------


def test_render_morning_report_shows_escalated_prs_with_links():
    pr = _open_pr(14, ticket_number=7, labels=("engine:escalated",))
    snapshot = WorldSnapshot(
        repo_name="owner/slackbot",
        tickets=[_ticket(7)],
        open_prs=[pr],
    )
    report = render_morning_report(_data([snapshot]))
    assert "escalated" in report.lower()
    assert "#14" in report
    assert "https://github.com/owner/slackbot/pull/14" in report


def test_render_morning_report_escalated_shows_ticket_title():
    pr = _open_pr(20, ticket_number=4, labels=("engine:escalated",))
    snapshot = WorldSnapshot(
        repo_name="owner/repo",
        tickets=[_ticket(4)],
        open_prs=[pr],
    )
    report = render_morning_report(_data([snapshot]))
    assert "Ticket 4" in report


# ---------------------------------------------------------------------------
# AC 3: held PRs appear with links
# ---------------------------------------------------------------------------


def test_render_morning_report_shows_held_prs_with_links():
    pr = _open_pr(15, ticket_number=8, labels=("engine:hold",))
    snapshot = WorldSnapshot(
        repo_name="owner/slackbot",
        tickets=[_ticket(8)],
        open_prs=[pr],
    )
    report = render_morning_report(_data([snapshot]))
    assert "held" in report.lower()
    assert "#15" in report
    assert "https://github.com/owner/slackbot/pull/15" in report


def test_render_morning_report_held_shows_ticket_title():
    pr = _open_pr(21, ticket_number=5, labels=("engine:hold",))
    snapshot = WorldSnapshot(
        repo_name="owner/repo",
        tickets=[_ticket(5)],
        open_prs=[pr],
    )
    report = render_morning_report(_data([snapshot]))
    assert "Ticket 5" in report


# ---------------------------------------------------------------------------
# AC 4: tickets waiting for the box appear (frontier tickets with runner=windows)
# ---------------------------------------------------------------------------


def test_render_morning_report_shows_windows_waiting_tickets():
    # windows ticket with no blockers → on frontier
    windows_ticket = _ticket(9, runner="windows")
    snapshot = WorldSnapshot(
        repo_name="owner/slackbot",
        tickets=[windows_ticket],
    )
    report = render_morning_report(_data([snapshot]))
    lines = report.splitlines()
    idx = lines.index("**Waiting for the box:** 1")
    assert lines[idx + 1] == "- 09: Ticket 9"


def test_render_morning_report_blocked_windows_ticket_not_shown():
    # windows ticket whose blocker is not done → NOT on frontier
    blocker = _ticket(1, status="in-progress")
    windows_ticket = _ticket(9, runner="windows", blocked_by=[1])
    snapshot = WorldSnapshot(
        repo_name="owner/repo",
        tickets=[blocker, windows_ticket],
    )
    report = render_morning_report(_data([snapshot]))
    # Ticket 9's blocker is in-progress, so it is not waiting for the box yet.
    lines = report.splitlines()
    idx = lines.index("**Waiting for the box:** 0")
    assert lines[idx + 1] == ""
    assert "- 09: Ticket 9" not in lines


# ---------------------------------------------------------------------------
# AC 5: paused repo is clearly shown
# ---------------------------------------------------------------------------


def test_render_morning_report_shows_paused_repo():
    snapshot = WorldSnapshot(
        repo_name="owner/tds-t8",
        paused=True,
    )
    report = render_morning_report(_data([snapshot]))
    assert "owner/tds-t8" in report
    assert "paused" in report.lower()


def test_render_morning_report_non_paused_repo_shows_no():
    snapshot = WorldSnapshot(repo_name="owner/active-repo")
    report = render_morning_report(_data([snapshot]))
    assert "owner/active-repo" in report
    # Non-paused repo must show "No", not "Yes"
    idx = report.find("owner/active-repo")
    section = report[idx : idx + 500]
    assert "**Paused:** No" in section or "paused:** no" in section.lower()
    assert "paused:** yes" not in section.lower()


# ---------------------------------------------------------------------------
# AC 6: parse findings appear
# ---------------------------------------------------------------------------


def test_render_morning_report_shows_parse_findings():
    ticket_with_finding = Ticket(
        number=3,
        title="Old ticket",
        slug="old-ticket",
        status="done",
        findings=[ParseFinding(message="Unknown status 'complete'", file="03-old.md")],
    )
    snapshot = WorldSnapshot(
        repo_name="owner/rbl",
        tickets=[ticket_with_finding],
    )
    report = render_morning_report(_data([snapshot]))
    assert "03-old.md" in report or "Unknown status" in report


def test_render_morning_report_no_findings_is_clean():
    snapshot = WorldSnapshot(
        repo_name="owner/rbl",
        tickets=[_ticket(1, status="done")],
    )
    report = render_morning_report(_data([snapshot]))
    assert "owner/rbl" in report
    # "None" or similar should appear when there are no findings


# ---------------------------------------------------------------------------
# AC 7: Jules quota standing
# ---------------------------------------------------------------------------


def test_render_morning_report_shows_jules_quota():
    snapshot = WorldSnapshot(repo_name="owner/slackbot")
    report = render_morning_report(_data([snapshot], jules_sessions_24h=45, jules_limit=100))
    assert "45" in report
    assert "100" in report


def test_render_morning_report_quota_shows_remaining():
    snapshot = WorldSnapshot(repo_name="owner/slackbot")
    report = render_morning_report(_data([snapshot], jules_sessions_24h=80, jules_limit=100))
    # 20 remaining
    assert "20" in report


# ---------------------------------------------------------------------------
# AC 8: multiple repos each get their own section
# ---------------------------------------------------------------------------


def test_render_morning_report_multiple_repos_each_have_section():
    s1 = WorldSnapshot(repo_name="owner/repo-a")
    s2 = WorldSnapshot(repo_name="owner/repo-b")
    report = render_morning_report(_data([s1, s2]))
    assert "owner/repo-a" in report
    assert "owner/repo-b" in report


# ---------------------------------------------------------------------------
# AC 9 (privacy): report never names a person or prints a secret
# ---------------------------------------------------------------------------


def test_render_morning_report_no_person_names():
    """The report must not include the developer's real name."""
    snapshot = WorldSnapshot(
        repo_name="owner/slackbot",
        merged_prs=[MergedPR(number=1, title="Some PR", ticket_number=1)],
    )
    report = render_morning_report(_data([snapshot]))
    # Only roles and generic labels appear — no hard-coded names
    for suspicious in ("Isaac", "ilegault", "PIPELINE_TOKEN", "JULES_API_KEY"):
        assert suspicious not in report


# ---------------------------------------------------------------------------
# AC: report header contains the date/time
# ---------------------------------------------------------------------------


def test_render_morning_report_contains_date_in_header():
    snapshot = WorldSnapshot(repo_name="owner/slackbot")
    report = render_morning_report(
        MorningReportData(
            repo_snapshots=[snapshot],
            jules_sessions_24h=0,
            jules_limit=100,
            now=datetime.datetime(2026, 9, 22, 12, 0, 0, tzinfo=datetime.UTC),
        )
    )
    assert "2026-09-22" in report


# ---------------------------------------------------------------------------
# AC: clean repo (nothing to report) still shows a section
# ---------------------------------------------------------------------------


def test_render_morning_report_clean_repo_shows_section():
    snapshot = WorldSnapshot(repo_name="owner/quiet-repo")
    report = render_morning_report(_data([snapshot]))
    assert "owner/quiet-repo" in report


# ---------------------------------------------------------------------------
# Ticket 17: Needs you (ready-for-developer)
# ---------------------------------------------------------------------------


def test_render_morning_report_needs_you_placement_and_count():
    t1 = _parsed_ticket(10, "ready-for-developer")
    t2 = _parsed_ticket(20, "ready-for-developer")
    snapshot = WorldSnapshot(
        repo_name="owner/repo",
        tickets=[t1, t2],
    )
    report = render_morning_report(_data([snapshot]))

    assert "**Needs you (ready-for-developer):** 2" in report

    held_idx = report.find("**Held (awaiting approval):**")
    needs_idx = report.find("**Needs you (ready-for-developer):** 2")
    windows_idx = report.find("**Waiting for the box:**")

    assert held_idx != -1
    assert needs_idx != -1
    assert windows_idx != -1
    assert held_idx < needs_idx < windows_idx

    # Lead line followed by table and trailing blank line before Waiting for the box
    lines = report.splitlines()
    needs_line_idx = lines.index("**Needs you (ready-for-developer):** 2")
    assert lines[needs_line_idx + 1] == "| Ticket | Title | Waiting on | Holding up |"
    windows_line_idx = lines.index("**Waiting for the box:** 0")
    assert lines[windows_line_idx - 1] == ""


def test_render_morning_report_needs_you_same_table_same_renderer():
    t1 = _parsed_ticket(34, "ready-for-agent")
    t2 = _parsed_ticket(36, "done")
    t3 = _parsed_ticket(43, "ready-for-developer", blocked_by="34, 36", title="Ticket 43")
    snapshot = WorldSnapshot(
        repo_name="owner/repo",
        tickets=[t1, t2, t3],
    )
    report = render_morning_report(_data([snapshot]))
    assert "| Ticket | Title | Waiting on | Holding up |" in report
    assert "|---|---|---|---|" in report
    assert _table_row(report, 43) == "| 43 | Ticket 43 | 34 (ready-for-agent) | — |"


def test_render_morning_report_needs_you_never_two_answers():
    t34 = _parsed_ticket(34, "ready-for-agent")
    t36 = _parsed_ticket(36, "done")
    t43 = _parsed_ticket(43, "ready-for-developer", blocked_by="34, 36", title="Ticket 43")
    tickets = [t34, t36, t43]
    snapshot = WorldSnapshot(repo_name="owner/repo", tickets=tickets)

    morning = render_morning_report(_data([snapshot]))
    run = build_run_report(tickets, RunFacts())

    expected = "| 43 | Ticket 43 | 34 (ready-for-agent) | — |"
    assert _table_row(morning, 43) == expected
    assert _table_row(morning, 43) == _table_row(run, 43)


def test_render_morning_report_needs_you_zero_shown_not_hidden():
    snapshot = WorldSnapshot(
        repo_name="owner/repo",
        tickets=[_parsed_ticket(1, "ready-for-agent")],
    )
    report = render_morning_report(_data([snapshot]))
    assert "**Needs you (ready-for-developer):** 0" in report
    assert "| Ticket | Title | Waiting on | Holding up |" not in report

    lines = report.splitlines()
    idx = lines.index("**Needs you (ready-for-developer):** 0")
    assert lines[idx + 1] == ""
    assert lines[idx + 2] == "**Waiting for the box:** 0"


# ---------------------------------------------------------------------------
# Ticket 27: the Box line, under the report's title
# ---------------------------------------------------------------------------

_BOX_NOW = datetime.datetime(2026, 9, 26, 12, 0, tzinfo=datetime.UTC)
_CHECKED_IN = datetime.datetime(2026, 9, 26, 3, 12, tzinfo=datetime.UTC)


def _box_report(box: BoxStatus | None) -> str:
    data = MorningReportData(
        repo_snapshots=[WorldSnapshot(repo_name="owner/repo")],
        now=_BOX_NOW,
    )
    return render_morning_report(data, box=box)


def _line_under_title(report: str) -> str:
    lines = report.splitlines()
    assert lines[0].startswith("## Morning Report — ")
    assert lines[1] == ""
    return lines[2]


def _box_lines(report: str) -> list[str]:
    return [ln for ln in report.splitlines() if ln.startswith("**Box:**")]


def test_render_morning_report_box_available():
    box = BoxStatus(checked_in_at=_CHECKED_IN, state=BoxState.idle)
    report = _box_report(box)
    expected = "**Box:** available — last check-in 2026-09-26 03:12 UTC"
    assert _line_under_title(report) == expected
    assert _box_lines(report) == [expected]


def test_render_morning_report_box_paused():
    box = BoxStatus(checked_in_at=_CHECKED_IN, state=BoxState.paused_quota)
    report = _box_report(box)
    expected = "**Box:** paused — last check-in 2026-09-26 03:12 UTC"
    assert _line_under_title(report) == expected
    assert _box_lines(report) == [expected]


def test_render_morning_report_box_silent():
    # Checked in 12 hours before the report: box_silent_hours (default 12) elapsed.
    checked_in = _BOX_NOW - datetime.timedelta(hours=12)
    box = BoxStatus(checked_in_at=checked_in, state=BoxState.working)
    report = _box_report(box)
    expected = "**Box:** silent — last check-in 2026-09-26 00:00 UTC"
    assert _line_under_title(report) == expected
    assert _box_lines(report) == [expected]


def test_render_morning_report_box_unreadable():
    report = _box_report(None)
    expected = "**Box:** status unreadable"
    assert _line_under_title(report) == expected
    assert _box_lines(report) == [expected]


def test_render_morning_report_box_uses_dispatch_classification():
    # One definition of the box's state: the morning report agrees with the dispatcher.
    for state in BoxState:
        box = BoxStatus(checked_in_at=_CHECKED_IN, state=state)
        word = classify_box(box, _BOX_NOW, RepoConfig().box_silent_hours)
        snapshot = WorldSnapshot(box=box, now=_BOX_NOW)
        assert DispatchCore().evaluate(snapshot).box_state == word
        assert _line_under_title(_box_report(box)).startswith(f"**Box:** {word} — ")


# ---------------------------------------------------------------------------
# Ticket 27: runner wiring — scripts/run_morning_report.py
# ---------------------------------------------------------------------------

_SCRIPT = pathlib.Path(__file__).resolve().parent.parent / "scripts" / "run_morning_report.py"


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_morning_report_under_test", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run_main(monkeypatch, box_issues) -> tuple[list[str], list[str]]:
    """Run the script's main with GitHub faked; return (updated bodies, requested paths)."""
    runner = _load_runner()
    requested: list[str] = []
    updated: list[str] = []

    def fake_gh_request(path, token, method="GET", body=None):
        requested.append(path)
        if "labels=engine:box-status" in path:
            if isinstance(box_issues, Exception):
                raise box_issues
            return box_issues
        if "labels=engine:morning-report" in path:
            return [{"title": "Morning Report", "number": 5}]
        if "/git/trees/" in path or "/actions/variables/" in path:
            return {}
        return []

    def fake_update_issue(engine_repo, token, issue_number, body):
        updated.append(body)

    monkeypatch.setenv("PIPELINE_TOKEN", "fake-token")
    monkeypatch.setenv("ENGINE_REPO", "owner/engine")
    monkeypatch.delenv("JULES_API_KEY", raising=False)
    monkeypatch.setattr(runner, "_gh_request", fake_gh_request)
    monkeypatch.setattr(runner, "_update_issue", fake_update_issue)

    assert runner.main() == 0
    return updated, requested


def test_run_morning_report_main_passes_box_status(monkeypatch):
    # A check-in years before any real clock the runner reads: always silent.
    checked_in = datetime.datetime(2020, 1, 1, 3, 12, tzinfo=datetime.UTC)
    body = render_box_status(BoxStatus(checked_in_at=checked_in, state=BoxState.idle))
    updated, requested = _run_main(
        monkeypatch, [{"number": 9, "title": "Box status", "body": body}]
    )
    assert any(
        p.startswith("/repos/owner/engine/issues") and "labels=engine:box-status" in p
        for p in requested
    )
    assert len(updated) == 1
    assert _line_under_title(updated[0]) == "**Box:** silent — last check-in 2020-01-01 03:12 UTC"


def test_run_morning_report_main_failed_box_fetch_is_unreadable(monkeypatch):
    error = urllib.error.URLError("network down")
    updated, _ = _run_main(monkeypatch, error)
    assert len(updated) == 1
    assert _line_under_title(updated[0]) == "**Box:** status unreadable"
