"""Tests for display_time: the one place times become Central time text.

WHY THIS EXISTS
---------------
Ticket 77 (ADR 0007 rule 4): the box status issue is read by the developer in
America/Chicago, so the text carries the zone abbreviation and the parser maps
it back to UTC.
"""
from __future__ import annotations

import datetime

from ticket_engine.display_time import format_display, parse_display


def test_format_display_uses_cdt_in_summer_and_cst_in_winter():
    summer = datetime.datetime(2026, 7, 1, 20, 11, tzinfo=datetime.UTC)
    winter = datetime.datetime(2026, 12, 1, 21, 11, tzinfo=datetime.UTC)
    assert format_display(summer) == "2026-07-01 3:11 PM CDT"
    assert format_display(winter) == "2026-12-01 3:11 PM CST"


def test_format_display_midnight_and_noon():
    assert format_display(datetime.datetime(2026, 7, 1, 5, 0, tzinfo=datetime.UTC)) == (
        "2026-07-01 12:00 AM CDT"
    )
    assert format_display(datetime.datetime(2026, 7, 1, 17, 0, tzinfo=datetime.UTC)) == (
        "2026-07-01 12:00 PM CDT"
    )


def test_parse_display_inverts_format_display():
    instants = [
        datetime.datetime(2026, 7, 1, 20, 11, tzinfo=datetime.UTC),
        datetime.datetime(2026, 12, 1, 21, 11, tzinfo=datetime.UTC),
        # The hour that happens twice when daylight saving ends: only the
        # CDT/CST suffix tells the two apart.
        datetime.datetime(2026, 11, 1, 6, 30, tzinfo=datetime.UTC),
        datetime.datetime(2026, 11, 1, 7, 30, tzinfo=datetime.UTC),
    ]
    for instant in instants:
        assert parse_display(format_display(instant)) == instant


def test_parse_display_returns_none_for_other_text():
    assert parse_display("") is None
    assert parse_display("2026-09-26T03:12Z") is None
    assert parse_display("2026-07-01 3:11 PM PST") is None
    assert parse_display("2026-13-01 3:11 PM CDT") is None
