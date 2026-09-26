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
    TicketRef,
    parse_box_status,
    render_box_alert,
    render_box_status,
    render_escalation_issue,
)

PUBLIC_LINE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^## Box status$"),
    re.compile(r"^Checked in: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$"),
    re.compile(r"^State: (working|idle|paused_quota|paused_weekly_cap|login_expired)$"),
    re.compile(r"^Current: (none|[\w.-]+/[\w.-]+ #\d+)$"),
    re.compile(r"^Paused until: (none|\d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z)$"),
    re.compile(r"^Box alert: (weekly cap reached|agy login expired|box silent)$"),
    re.compile(r"^@[\w.-]+$"),
    re.compile(r"^Since: \d{4}-\d{2}-\d{2}T\d{2}:\d{2}Z$"),
    re.compile(r"^Escalation: [\w.-]+-\d+ [\w.-]+$"),
    re.compile(r"^Ticket: [\w.-]+/[\w.-]+ #\d+$"),
    re.compile(r"^Link: https://github\.com/\S+$"),
    re.compile(r"^Reason: (ci_failed|kept_asking|resumes_exhausted)$"),
)


# ---------------------------------------------------------------------------
# Criterion 1: Types
# ---------------------------------------------------------------------------


def test_box_state_enum_values():
    assert issubclass(BoxState, str)
    expected = {"working", "idle", "paused_quota", "paused_weekly_cap", "login_expired"}
    assert {s.value for s in BoxState} == expected
    assert len(BoxState) == 5
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
        "Checked in: 2026-09-26T03:12Z\n"
        "State: working\n"
        "Current: owner/repo #01\n"
        "Paused until: none\n"
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
        "Checked in: 2026-09-26T03:12Z\n"
        "State: paused_quota\n"
        "Current: none\n"
        "Paused until: 2026-09-26T08:00Z\n"
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
        assert body == "@owner\nSince: 2026-09-26T03:12Z\n"

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
