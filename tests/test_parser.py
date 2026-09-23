"""Tests for the ticket markdown parser.

WHY THIS EXISTS
---------------
ADR 0001 and Phase 1 Spec §Implementation Decisions (Ticket parsing) require
that tickets across all target repos are parsed mechanically and consistently.
Past repos allowed drifting status words (like 'complete', 'human-task') and
differing formatting (bold vs plain). This test suite ensures the parser
strictly validates statuses, parses dependencies and runners, and reports
legacy or unknown words as findings rather than silently accepting them.
"""
from __future__ import annotations

import pytest

from ticket_engine.parser import TicketParser


def test_parser_reads_plain_metadata():
    content = """# 05: Fix production bug
Status: ready-for-agent
Blocked by: 01, 02
Runner: any
Auto-merge: yes

## Acceptance criteria
- [ ] Criteria 1
"""
    parser = TicketParser()
    ticket = parser.parse_text(content, filename="05-fix-production-bug.md")

    assert ticket.number == 5
    assert ticket.slug == "fix-production-bug"
    assert ticket.title == "Fix production bug"
    assert ticket.status == "ready-for-agent"
    assert ticket.blocked_by == [1, 2]
    assert ticket.runner == "any"
    assert ticket.auto_merge is True
    assert ticket.findings == []


def test_parser_reads_bold_metadata():
    content = """# 12: Add card editing
**Status:** ready-for-developer
**Blocked by:** 10
**Runner:** windows
**Auto-merge:** no

## Acceptance criteria
- [ ] Item 1
"""
    parser = TicketParser()
    ticket = parser.parse_text(content, filename="12-add-card-editing.md")

    assert ticket.number == 12
    assert ticket.title == "Add card editing"
    assert ticket.status == "ready-for-developer"
    assert ticket.blocked_by == [10]
    assert ticket.runner == "windows"
    assert ticket.auto_merge is False
    assert ticket.findings == []


def test_parser_blocked_by_none_means_no_blockers():
    content = """# 01: Engine skeleton
**Blocked by:** None (can start immediately)
**Status:** ready-for-agent
"""
    parser = TicketParser()
    ticket = parser.parse_text(content, filename="01-engine-skeleton.md")

    assert ticket.blocked_by == []
    assert ticket.status == "ready-for-agent"


def test_parser_missing_runner_defaults_to_any():
    content = """# 02: Next feature
Status: ready-for-agent
Blocked by: None
"""
    parser = TicketParser()
    ticket = parser.parse_text(content, filename="02-next-feature.md")

    assert ticket.runner == "any"


def test_parser_missing_auto_merge_defaults_to_yes():
    content = """# 03: Feature three
Status: ready-for-agent
Blocked by: None
"""
    parser = TicketParser()
    ticket = parser.parse_text(content, filename="03-feature-three.md")

    assert ticket.auto_merge is True


@pytest.mark.parametrize(
    "legacy_status",
    [
        "complete",
        "completed",
        "human-task",
        "human-task done",
        "in_progress",
        "unknown-status",
    ],
)
def test_unknown_or_legacy_status_is_reported_as_finding_and_never_done(legacy_status: str):
    content = f"""# 34: Server folder setup
**Status:** {legacy_status}
**Blocked by:** 26
"""
    parser = TicketParser()
    ticket = parser.parse_text(content, filename="34-server-folder-setup.md")

    assert ticket.is_done() is False
    assert len(ticket.findings) >= 1
    assert any("status" in f.message.lower() for f in ticket.findings)


def test_all_five_valid_statuses_are_accepted():
    valid_statuses = [
        "ready-for-agent",
        "ready-for-developer",
        "in-progress",
        "blocked",
        "done",
    ]
    parser = TicketParser()
    for s in valid_statuses:
        content = f"""# 01: Ticket
Status: {s}
Blocked by: None
"""
        ticket = parser.parse_text(content, filename="01-ticket.md")
        assert ticket.status == s
        assert ticket.findings == []
        if s == "done":
            assert ticket.is_done() is True
        else:
            assert ticket.is_done() is False
