"""Tests for the box_status pure module.

WHY THIS EXISTS
---------------
Ticket 20 AC: A new pure module owning every public box text (status issue,
box alerts, escalation issues) with typed inputs only, no free text, and a test
that every rendered line matches an allowlist.
ADR 0007 rule 4, ADR 0002.
"""
from __future__ import annotations

import dataclasses
import datetime
import re

import pytest

from ticket_engine.box_status import (
    AlertKind,
    BoxState,
    BoxStatus,
    EscalationReason,
    LastPR,
    NotReady,
    NotReadyReason,
    TicketRef,
    parse_box_status,
    parse_escalation_issue_title,
    render_box_alert,
    render_box_status,
    render_escalation_issue,
    render_repo_not_ready_alert,
)

PUBLIC_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^## Box status$"),
    re.compile(r"^Checked in: \d{4}-\d{2}-\d{2} \d{1,2}:\d{2} (?:AM|PM) (?:CDT|CST)$"),
    re.compile(r"^State: (working|idle|paused_quota|paused_weekly_cap|login_expired|paused_by_developer)$"),
    re.compile(r"^Current: (none|[\w.-]+/[\w.-]+ #\d+)$"),
    re.compile(
        r"^Step: (none|implementing|waiting for quota"
        r"|pre-push gate red \(resume \d+/\d+\)|fixing CI \(\d+/\d+\))$"
    ),
    re.compile(r"^Last PR: (none|[\w.-]+/[\w.-]+ #\d+, PR #\d+, \d{4}-\d{2}-\d{2} \d{1,2}:\d{2} (?:AM|PM) (?:CDT|CST))$"),
    re.compile(
        r"^Started \(24h\): (none|[\w.-]+/[\w.-]+ \d+/\d+"
        r"(, [\w.-]+/[\w.-]+ \d+/\d+)*)$"
    ),
    re.compile(r"^Paused until: (none|\d{4}-\d{2}-\d{2} \d{1,2}:\d{2} (?:AM|PM) (?:CDT|CST))$"),
    re.compile(r"^Not ready: .+$"),
    re.compile(r"^Box alert: (weekly cap reached|agy login expired|box silent|[\w.-]+/[\w.-]+ not ready)$"),
    re.compile(r"^@[\w.-]+$"),
    re.compile(r"^Since: \d{4}-\d{2}-\d{2} \d{1,2}:\d{2} (?:AM|PM) (?:CDT|CST)$"),
    re.compile(r"^Escalation: [\w.-]+-\d+ [\w.-]+$"),
    re.compile(r"^Ticket: [\w.-]+/[\w.-]+ #\d+$"),
    re.compile(r"^Link: https://github\.com/\S+$"),
    re.compile(r"^Reason: (ci_failed|kept_asking|resumes_exhausted|clone_failed|no_token_access|no_engine_config|env_failed|baseline_red)$"),
)


# ---------------------------------------------------------------------------
# Criterion 1: Types
# ---------------------------------------------------------------------------


def test_box_state_enum_values():
    assert issubclass(BoxState, str)
    expected = {"working", "idle", "paused_quota", "paused_weekly_cap", "login_expired", "paused_by_developer"}
    assert {s.value for s in BoxState} == expected
    assert len(BoxState) == 6
    for val in expected:
        assert BoxState(val) == val


def test_ticket_ref_dataclass_and_validation():
    ref = TicketRef(repo="owner/repo", number=20)
    assert ref.repo == "owner/repo"
    assert ref.number == 20
    assert dataclasses.is_dataclass(ref)

    # Frozen
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        ref.repo = "other/repo"  # type: ignore

    # Validation of repo
    with pytest.raises(ValueError):
        TicketRef(repo="a/b c", number=1)

    with pytest.raises(ValueError):
        TicketRef(repo="a/b\nx", number=1)

    with pytest.raises(ValueError):
        TicketRef(repo="invalid-repo-without-slash", number=1)

    with pytest.raises(ValueError):
        TicketRef(repo="", number=1)


def test_box_status_dataclass_frozen_and_aware():
    now = datetime.datetime(2026, 9, 26, 3, 12, tzinfo=datetime.UTC)
    status = BoxStatus(
        checked_in_at=now,
        state=BoxState.working,
        current=TicketRef("owner/repo", 20),
        paused_until=None,
    )
    assert dataclasses.is_dataclass(status)
    assert status.checked_in_at == now
    assert status.state == BoxState.working
    assert status.current == TicketRef("owner/repo", 20)
    assert status.paused_until is None

    # Frozen
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        status.state = BoxState.idle  # type: ignore

    # Naive datetime raises ValueError
    naive = datetime.datetime(2026, 9, 26, 3, 12, tzinfo=None)  # noqa: DTZ001
    with pytest.raises(ValueError):
        BoxStatus(
            checked_in_at=naive,
            state=BoxState.working,
            current=None,
            paused_until=None,
        )


# ---------------------------------------------------------------------------
# Criterion 2: Round trip and exact rendering
# ---------------------------------------------------------------------------


def test_render_box_status_exact_body():
    checked_in = datetime.datetime(2026, 9, 26, 3, 12, tzinfo=datetime.UTC)
    s = BoxStatus(
        checked_in_at=checked_in,
        state=BoxState.working,
        current=TicketRef(repo="owner/repo", number=1),
        paused_until=None,
    )
    expected = (
        "## Box status\n"
        "Checked in: 2026-09-25 10:12 PM CDT\n"
        "State: working\n"
        "Current: owner/repo #01\n"
        "Step: none\n"
        "Last PR: none\n"
        "Started (24h): none\n"
        "Paused until: none\n"
        "Not ready: none\n"
    )
    assert render_box_status(s) == expected


def test_render_box_status_paused_body():
    checked_in = datetime.datetime(2026, 9, 26, 3, 12, tzinfo=datetime.UTC)
    paused = datetime.datetime(2026, 9, 26, 8, 0, tzinfo=datetime.UTC)
    s = BoxStatus(
        checked_in_at=checked_in,
        state=BoxState.paused_quota,
        current=None,
        paused_until=paused,
    )
    expected = (
        "## Box status\n"
        "Checked in: 2026-09-25 10:12 PM CDT\n"
        "State: paused_quota\n"
        "Current: none\n"
        "Step: none\n"
        "Last PR: none\n"
        "Started (24h): none\n"
        "Paused until: 2026-09-26 3:00 AM CDT\n"
        "Not ready: none\n"
    )
    assert render_box_status(s) == expected


def test_box_status_round_trip_all_states_with_and_without_optionals():
    base_time = datetime.datetime(2026, 9, 26, 12, 34, 45, tzinfo=datetime.UTC)
    paused_time = datetime.datetime(2026, 9, 26, 14, 0, 15, tzinfo=datetime.UTC)
    truncated_base = base_time.replace(second=0, microsecond=0)
    truncated_paused = paused_time.replace(second=0, microsecond=0)

    for state in BoxState:
        # Without current and without paused_until
        s1 = BoxStatus(
            checked_in_at=truncated_base,
            state=state,
            current=None,
            paused_until=None,
        )
        assert parse_box_status(render_box_status(s1)) == s1

        # With current and without paused_until
        s2 = BoxStatus(
            checked_in_at=truncated_base,
            state=state,
            current=TicketRef("owner/repo", 20),
            paused_until=None,
        )
        assert parse_box_status(render_box_status(s2)) == s2

        # Without current and with paused_until
        s3 = BoxStatus(
            checked_in_at=truncated_base,
            state=state,
            current=None,
            paused_until=truncated_paused,
        )
        assert parse_box_status(render_box_status(s3)) == s3

        # With current and with paused_until
        s4 = BoxStatus(
            checked_in_at=truncated_base,
            state=state,
            current=TicketRef("owner/repo", 20),
            paused_until=truncated_paused,
        )
        assert parse_box_status(render_box_status(s4)) == s4

        # Truncation check: s with seconds matches parse_box_status(render_box_status(s))
        s_with_seconds = BoxStatus(
            checked_in_at=base_time,
            state=state,
            current=TicketRef("owner/repo", 20),
            paused_until=paused_time,
        )
        parsed = parse_box_status(render_box_status(s_with_seconds))
        assert parsed == s_with_seconds


# ---------------------------------------------------------------------------
# Criterion 3: Unreadable bodies
# ---------------------------------------------------------------------------


def test_parse_box_status_unreadable_bodies_return_none():
    assert parse_box_status("") is None
    assert parse_box_status("   \n\n  ") is None

    # Missing Checked in:
    no_checked_in = (
        "## Box status\n"
        "State: working\n"
        "Current: none\n"
        "Paused until: none\n"
    )
    assert parse_box_status(no_checked_in) is None

    # Unknown state word
    unknown_state = (
        "## Box status\n"
        "Checked in: 2026-09-26T03:12Z\n"
        "State: dancing\n"
        "Current: none\n"
        "Paused until: none\n"
    )
    assert parse_box_status(unknown_state) is None

    # Malformed timestamp in Checked in:
    malformed_ts = (
        "## Box status\n"
        "Checked in: 2026-99-99T99:99Z\n"
        "State: working\n"
        "Current: none\n"
        "Paused until: none\n"
    )
    assert parse_box_status(malformed_ts) is None

    # Malformed timestamp in Paused until:
    malformed_paused = (
        "## Box status\n"
        "Checked in: 2026-09-26T03:12Z\n"
        "State: paused_quota\n"
        "Current: none\n"
        "Paused until: not-a-date\n"
    )
    assert parse_box_status(malformed_paused) is None

    # Missing heading
    no_heading = (
        "Checked in: 2026-09-26T03:12Z\n"
        "State: working\n"
        "Current: none\n"
        "Paused until: none\n"
    )
    assert parse_box_status(no_heading) is None

    # Malformed current
    malformed_current = (
        "## Box status\n"
        "Checked in: 2026-09-26T03:12Z\n"
        "State: working\n"
        "Current: invalid_current\n"
        "Paused until: none\n"
    )
    assert parse_box_status(malformed_current) is None

    # Garbage
    assert parse_box_status("random text\nwith\nmultiple\nlines\n") is None


# ---------------------------------------------------------------------------
# Criterion 4: Alerts and escalation issues
# ---------------------------------------------------------------------------


def test_alert_kind_enum():
    assert issubclass(AlertKind, str)
    expected = {"weekly_cap", "login_expired", "box_silent"}
    assert {k.value for k in AlertKind} == expected
    assert len(AlertKind) == 3


def test_render_box_alert_titles_and_bodies():
    since = datetime.datetime(2026, 9, 26, 3, 12, tzinfo=datetime.UTC)
    cases = [
        (AlertKind.weekly_cap, "Box alert: weekly cap reached"),
        (AlertKind.login_expired, "Box alert: agy login expired"),
        (AlertKind.box_silent, "Box alert: box silent"),
    ]
    for kind, expected_title in cases:
        title, body = render_box_alert(kind, "owner", since)
        assert title == expected_title
        assert body == "@owner\nSince: 2026-09-25 10:12 PM CDT\n"

    # Validation
    with pytest.raises(ValueError):
        render_box_alert(AlertKind.weekly_cap, "owner with space", since)

    with pytest.raises(ValueError):
        render_box_alert(AlertKind.weekly_cap, "owner\nx", since)


def test_render_escalation_issue():
    ref = TicketRef("owner/repo", 20)
    title, body = render_escalation_issue(
        ref=ref,
        effort="box-primary-worker",
        title_slug="box-status-texts",
        link="https://github.com/owner/repo/pull/42",
        reason=EscalationReason.ci_failed,
        owner="repo-owner",
    )
    assert title == "Escalation: box-primary-worker-20 box-status-texts"
    expected_body = (
        "@repo-owner\n"
        "Ticket: owner/repo #20\n"
        "Link: https://github.com/owner/repo/pull/42\n"
        "Reason: ci_failed\n"
    )
    assert body == expected_body

    # Test all reasons
    for reason in EscalationReason:
        _t, b = render_escalation_issue(
            ref=ref,
            effort="effort",
            title_slug="slug",
            link="https://github.com/owner/repo/pull/1",
            reason=reason,
            owner="owner",
        )
        assert f"Reason: {reason.value}" in b

    # Validation
    with pytest.raises(ValueError):
        render_escalation_issue(
            ref=ref,
            effort="effort",
            title_slug="slug",
            link="http://insecure.com/foo",
            reason=EscalationReason.ci_failed,
            owner="owner",
        )

    with pytest.raises(ValueError):
        render_escalation_issue(
            ref=ref,
            effort="effort",
            title_slug="invalid slug",
            link="https://github.com/owner/repo/pull/1",
            reason=EscalationReason.ci_failed,
            owner="owner",
        )

    with pytest.raises(ValueError):
        render_escalation_issue(
            ref=ref,
            effort="effort",
            title_slug="slug",
            link="https://github.com/owner/repo/pull/1",
            reason=EscalationReason.ci_failed,
            owner="owner with space",
        )


def test_parse_escalation_issue_title_round_trips_render_escalation_issue():
    ref = TicketRef("owner/repo", 26)
    title, _body = render_escalation_issue(
        ref=ref,
        effort="box-primary-worker",
        title_slug="escalation-issues-from-the-dispatcher",
        link="https://github.com/owner/repo/pull/42",
        reason=EscalationReason.ci_failed,
        owner="owner",
    )
    assert parse_escalation_issue_title(title) == (
        "box-primary-worker",
        26,
        "escalation-issues-from-the-dispatcher",
    )


def test_parse_escalation_issue_title_returns_none_for_unrecognised_text():
    assert parse_escalation_issue_title("") is None
    assert parse_escalation_issue_title("Some other issue title") is None
    assert parse_escalation_issue_title("Escalation: missing-the-number") is None


# ---------------------------------------------------------------------------
# Criterion 5: Public text allowlist test
# ---------------------------------------------------------------------------


def test_every_public_box_line_is_on_the_allowlist():
    ref = TicketRef("owner/repo", 20)
    since = datetime.datetime(2026, 9, 26, 3, 12, tzinfo=datetime.UTC)
    all_lines: list[str] = []

    # 1. Render all status states (with and without current and paused_until)
    for state in BoxState:
        s1 = BoxStatus(checked_in_at=since, state=state, current=ref, paused_until=since)
        all_lines.extend(render_box_status(s1).splitlines())

        s2 = BoxStatus(checked_in_at=since, state=state, current=None, paused_until=None)
        all_lines.extend(render_box_status(s2).splitlines())

    # 2. Render all alert kinds
    for kind in AlertKind:
        title, body = render_box_alert(kind, "owner", since)
        all_lines.append(title)
        all_lines.extend(body.splitlines())

    # 3. Render all escalation reasons
    for reason in EscalationReason:
        title, body = render_escalation_issue(
            ref=ref,
            effort="box-primary-worker",
            title_slug="slug",
            link="https://github.com/owner/repo/pull/1",
            reason=reason,
            owner="owner",
        )
        all_lines.append(title)
        all_lines.extend(body.splitlines())

    assert all_lines, "Must have generated lines to check"
    for line in all_lines:
        assert any(
            pat.fullmatch(line) for pat in PUBLIC_LINE_PATTERNS
        ), f"Line not on allowlist: {line!r}"


def test_not_ready_dataclass_and_reason_enum():
    assert issubclass(NotReadyReason, str)
    expected_reasons = {
        "clone_failed",
        "no_token_access",
        "no_engine_config",
        "env_failed",
        "baseline_red",
    }
    assert {r.value for r in NotReadyReason} == expected_reasons
    assert len(NotReadyReason) == 5

    nr = NotReady(repo="owner/repo", reason=NotReadyReason.baseline_red)
    assert nr.repo == "owner/repo"
    assert nr.reason == NotReadyReason.baseline_red
    assert dataclasses.is_dataclass(nr)

    # String coercion
    nr_str = NotReady(repo="owner/repo", reason="env_failed")  # type: ignore[arg-type]
    assert nr_str.reason == NotReadyReason.env_failed

    # Frozen
    with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
        nr.repo = "other/repo"  # type: ignore

    # Validation of repo
    with pytest.raises(ValueError):
        NotReady(repo="invalid-repo", reason=NotReadyReason.baseline_red)
    with pytest.raises(ValueError):
        NotReady(repo="", reason=NotReadyReason.baseline_red)

    # Validation of reason
    with pytest.raises(ValueError):
        NotReady(repo="owner/repo", reason="invalid_reason")  # type: ignore[arg-type]


def test_render_box_status_lists_not_ready_repos():
    checked_in = datetime.datetime(2026, 10, 6, 12, 0, tzinfo=datetime.UTC)
    s = BoxStatus(
        checked_in_at=checked_in,
        state=BoxState.working,
        current=None,
        paused_until=None,
        not_ready=(
            NotReady("owner/a", NotReadyReason.baseline_red),
            NotReady("owner/b", NotReadyReason.env_failed),
        ),
    )
    expected = (
        "## Box status\n"
        "Checked in: 2026-10-06 7:00 AM CDT\n"
        "State: working\n"
        "Current: none\n"
        "Step: none\n"
        "Last PR: none\n"
        "Started (24h): none\n"
        "Paused until: none\n"
        "Not ready: owner/a (baseline_red), owner/b (env_failed)\n"
    )
    assert render_box_status(s) == expected


def test_parse_box_status_round_trips_not_ready():
    checked_in = datetime.datetime(2026, 10, 6, 12, 0, tzinfo=datetime.UTC)
    s = BoxStatus(
        checked_in_at=checked_in,
        state=BoxState.working,
        current=TicketRef("owner/repo", 5),
        paused_until=None,
        not_ready=(
            NotReady("owner/a", NotReadyReason.baseline_red),
            NotReady("owner/b", NotReadyReason.env_failed),
        ),
    )
    rendered = render_box_status(s)
    parsed = parse_box_status(rendered)
    assert parsed == s


def test_parse_box_status_without_not_ready_line_gives_empty_tuple():
    text = (
        "## Box status\n"
        "Checked in: 2026-10-06T12:00Z\n"
        "State: working\n"
        "Current: none\n"
        "Paused until: none\n"
    )
    parsed = parse_box_status(text)
    assert parsed is not None
    assert parsed.not_ready == ()


def test_parse_box_status_malformed_not_ready_returns_none():
    base = (
        "## Box status\n"
        "Checked in: 2026-10-06T12:00Z\n"
        "State: working\n"
        "Current: none\n"
        "Paused until: none\n"
    )
    malformed_cases = [
        base + "Not ready:\n",
        base + "Not ready: not-a-valid-entry\n",
        base + "Not ready: owner/a\n",
        base + "Not ready: owner/a ()\n",
        base + "Not ready: owner/a (invalid_reason)\n",
        base + "Not ready: invalidrepo (baseline_red)\n",
        base + "Not ready: owner/a (baseline_red), invalid-second\n",
    ]
    for case in malformed_cases:
        assert parse_box_status(case) is None


def test_render_repo_not_ready_alert_exact_text_and_rejects_free_text():
    since = datetime.datetime(2026, 10, 6, 12, 0, tzinfo=datetime.UTC)
    title, body = render_repo_not_ready_alert(
        repo="owner/repo",
        reason=NotReadyReason.baseline_red,
        owner="developer",
        since=since,
    )
    assert title == "Box alert: owner/repo not ready"
    assert body == "@developer\nReason: baseline_red\nSince: 2026-10-06 7:00 AM CDT\n"

    # String reason works too
    title2, body2 = render_repo_not_ready_alert(
        repo="owner/repo",
        reason="baseline_red",
        owner="developer",
        since=since,
    )
    assert title2 == title
    assert body2 == body

    # Free text raises ValueError
    with pytest.raises(ValueError):
        render_repo_not_ready_alert(
            repo="owner/repo",
            reason="Traceback (most recent call last):",
            owner="developer",
            since=since,
        )

    # Invalid repo
    with pytest.raises(ValueError):
        render_repo_not_ready_alert(
            repo="bad-repo",
            reason=NotReadyReason.baseline_red,
            owner="developer",
            since=since,
        )

    # Invalid owner
    with pytest.raises(ValueError):
        render_repo_not_ready_alert(
            repo="owner/repo",
            reason=NotReadyReason.baseline_red,
            owner="bad owner",
            since=since,
        )


# ---------------------------------------------------------------------------
# Ticket 77: step, last PR, starts, Central time
# ---------------------------------------------------------------------------


def test_box_status_round_trips_step_last_pr_and_starts():
    # One side of the daylight-saving switch (CDT), then the other (CST).
    for checked_in, opened in (
        (
            datetime.datetime(2026, 10, 7, 20, 11, tzinfo=datetime.UTC),
            datetime.datetime(2026, 10, 7, 18, 5, tzinfo=datetime.UTC),
        ),
        (
            datetime.datetime(2026, 12, 1, 21, 11, tzinfo=datetime.UTC),
            datetime.datetime(2026, 11, 2, 7, 30, tzinfo=datetime.UTC),
        ),
    ):
        s = BoxStatus(
            checked_in_at=checked_in,
            state=BoxState.working,
            current=TicketRef("ilegault/slackbot", 101),
            step="fixing CI (2/3)",
            last_pr=LastPR(
                ref=TicketRef("ilegault/slackbot", 102), pr_number=124, opened_at=opened
            ),
            starts=(("ilegault/slackbot", 3, 10), ("ilegault/tds-t8", 0, 10)),
            paused_until=checked_in + datetime.timedelta(hours=5),
        )
        assert parse_box_status(render_box_status(s)) == s


def test_render_box_status_shows_the_new_layout_in_central_time():
    s = BoxStatus(
        checked_in_at=datetime.datetime(2026, 10, 7, 20, 11, tzinfo=datetime.UTC),
        state=BoxState.working,
        current=TicketRef("ilegault/slackbot", 101),
        step="fixing CI (2/3)",
        last_pr=LastPR(
            ref=TicketRef("ilegault/slackbot", 102),
            pr_number=124,
            opened_at=datetime.datetime(2026, 10, 7, 18, 5, tzinfo=datetime.UTC),
        ),
        starts=(("ilegault/slackbot", 3, 10), ("ilegault/tds-t8", 0, 10)),
    )
    assert render_box_status(s) == (
        "## Box status\n"
        "Checked in: 2026-10-07 3:11 PM CDT\n"
        "State: working\n"
        "Current: ilegault/slackbot #101\n"
        "Step: fixing CI (2/3)\n"
        "Last PR: ilegault/slackbot #102, PR #124, 2026-10-07 1:05 PM CDT\n"
        "Started (24h): ilegault/slackbot 3/10, ilegault/tds-t8 0/10\n"
        "Paused until: none\n"
        "Not ready: none\n"
    )


def test_parse_box_status_still_reads_the_old_utc_body():
    old_body = (
        "## Box status\n"
        "Checked in: 2026-09-26T03:12Z\n"
        "State: working\n"
        "Current: owner/repo #01\n"
        "Paused until: 2026-09-26T08:00Z\n"
    )
    parsed = parse_box_status(old_body)
    assert parsed is not None
    assert parsed.checked_in_at == datetime.datetime(2026, 9, 26, 3, 12, tzinfo=datetime.UTC)
    assert parsed.state == BoxState.working
    assert parsed.current == TicketRef("owner/repo", 1)
    assert parsed.paused_until == datetime.datetime(2026, 9, 26, 8, 0, tzinfo=datetime.UTC)
    assert parsed.step == "none"
    assert parsed.last_pr is None
    assert parsed.starts == ()


def test_box_status_rejects_a_free_text_step():
    with pytest.raises(ValueError):
        BoxStatus(
            checked_in_at=datetime.datetime(2026, 10, 7, 20, 11, tzinfo=datetime.UTC),
            state=BoxState.working,
            step="Traceback (most recent call last)",
        )


# ---------------------------------------------------------------------------
# Ticket 78: alerts show Central time
# ---------------------------------------------------------------------------


def test_box_alert_since_is_central():
    since = datetime.datetime(2026, 7, 1, 20, 11, tzinfo=datetime.UTC)
    _, body = render_box_alert(AlertKind.weekly_cap, "owner", since)
    assert "Since: 2026-07-01 3:11 PM CDT\n" in body
    _, body = render_repo_not_ready_alert(
        repo="owner/repo",
        reason=NotReadyReason.baseline_red,
        owner="owner",
        since=since,
    )
    assert "Since: 2026-07-01 3:11 PM CDT\n" in body
