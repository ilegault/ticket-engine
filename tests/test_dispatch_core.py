"""Tests for the pure dispatch core.

WHY THIS EXISTS
---------------
ADR 0001, ADR 0003, and Phase 1 Spec §Implementation Decisions (Dispatch core)
mandate that the dispatch core must be a pure function: given a world snapshot,
it returns actions with zero I/O. This test suite verifies ticket frontier
calculation, ordering, dependency gating, Windows worker routing, concurrency
limits, and status handling from the outside.
"""
from __future__ import annotations

import datetime

from ticket_engine.box_status import BoxState, BoxStatus, NotReady, NotReadyReason
from ticket_engine.dispatch import (
    DispatchCore,
    StartTicketAction,
    WorldSnapshot,
    classify_box,
)
from ticket_engine.parser import ParseFinding, Ticket


def make_ticket(
    number: int,
    status: str = "ready-for-agent",
    blocked_by: list[int] | None = None,
    runner: str = "any",
    auto_merge: bool = True,
    findings: list[ParseFinding] | None = None,
) -> Ticket:
    return Ticket(
        number=number,
        title=f"Ticket {number}",
        slug=f"ticket-{number}",
        status=status,
        blocked_by=blocked_by or [],
        runner=runner,
        auto_merge=auto_merge,
        findings=findings or [],
    )


def test_frontier_selects_ready_for_agent_with_all_blockers_done_ordered_by_number():
    tickets = [
        make_ticket(1, status="done"),
        make_ticket(3, status="ready-for-agent", blocked_by=[1]),
        make_ticket(2, status="ready-for-agent", blocked_by=[1]),
        make_ticket(4, status="ready-for-agent", blocked_by=[99]),  # blocker 99 not done
    ]
    core = DispatchCore()
    snapshot = WorldSnapshot(tickets=tickets, concurrency_limit=2)

    frontier = core.compute_frontier(snapshot)
    assert [t.number for t in frontier] == [2, 3]


def test_ready_for_developer_never_appears_on_the_frontier():
    tickets = [
        make_ticket(1, status="done"),
        make_ticket(2, status="ready-for-developer", blocked_by=[1]),
        make_ticket(3, status="ready-for-agent", blocked_by=[1]),
    ]
    core = DispatchCore()
    snapshot = WorldSnapshot(tickets=tickets, concurrency_limit=2)

    frontier = core.compute_frontier(snapshot)
    assert [t.number for t in frontier] == [3]


def test_scenario_fresh_ticket_set():
    tickets = [
        make_ticket(1, status="ready-for-agent", blocked_by=[]),
        make_ticket(2, status="ready-for-agent", blocked_by=[1]),
        make_ticket(3, status="ready-for-agent", blocked_by=[2]),
    ]
    core = DispatchCore()
    snapshot = WorldSnapshot(tickets=tickets, concurrency_limit=2)

    result = core.evaluate(snapshot)
    assert [t.number for t in result.frontier] == [1]
    assert len(result.actions) == 1
    assert isinstance(result.actions[0], StartTicketAction)
    assert result.actions[0].ticket.number == 1


def test_scenario_partial_completion():
    tickets = [
        make_ticket(1, status="done"),
        make_ticket(2, status="ready-for-agent", blocked_by=[1]),
        make_ticket(3, status="ready-for-agent", blocked_by=[1]),
        make_ticket(4, status="ready-for-agent", blocked_by=[2]),
    ]
    core = DispatchCore()
    snapshot = WorldSnapshot(tickets=tickets, concurrency_limit=2)

    result = core.evaluate(snapshot)
    assert [t.number for t in result.frontier] == [2, 3]
    assert len(result.actions) == 2
    assert [a.ticket.number for a in result.actions if isinstance(a, StartTicketAction)] == [2, 3]


def test_scenario_held_or_unmerged_blocker_keeps_dependents_off_the_frontier():
    # Ticket 1 is in-progress / blocked / not done
    tickets = [
        make_ticket(1, status="in-progress"),
        make_ticket(2, status="ready-for-agent", blocked_by=[1]),
        make_ticket(3, status="ready-for-agent", blocked_by=[]),  # independent ticket
    ]
    core = DispatchCore()
    snapshot = WorldSnapshot(tickets=tickets, concurrency_limit=2)

    result = core.evaluate(snapshot)
    assert [t.number for t in result.frontier] == [3]
    assert len(result.actions) == 1
    assert result.actions[0].ticket.number == 3


def test_scenario_windows_ticket_is_skipped_by_jules_dispatcher_and_reported():
    tickets = [
        make_ticket(1, status="ready-for-agent", runner="windows"),
        make_ticket(2, status="ready-for-agent", runner="any"),
    ]
    core = DispatchCore()
    snapshot = WorldSnapshot(tickets=tickets, concurrency_limit=2)

    result = core.evaluate(snapshot)
    # Both are on the frontier
    assert [t.number for t in result.frontier] == [1, 2]
    # But windows ticket is identified as skipped for cloud runner
    assert [t.number for t in result.skipped_windows_tickets] == [1]
    # And start action only starts ticket 2
    assert len(result.actions) == 1
    assert result.actions[0].ticket.number == 2


def test_scenario_legacy_status_is_never_done_and_reported_in_findings():
    finding = ParseFinding(message="Unknown status 'human-task'", file="01-legacy.md")
    tickets = [
        make_ticket(1, status="human-task", findings=[finding]),
        make_ticket(2, status="ready-for-agent", blocked_by=[1]),
    ]
    core = DispatchCore()
    snapshot = WorldSnapshot(tickets=tickets, concurrency_limit=2)

    result = core.evaluate(snapshot)
    # Ticket 2 should not be on the frontier because ticket 1 is not done
    assert result.frontier == []
    assert len(result.actions) == 0
    assert len(result.findings) == 1
    assert result.findings[0].message == "Unknown status 'human-task'"


def test_scenario_concurrency_limit_reached():
    tickets = [
        make_ticket(1, status="ready-for-agent"),
        make_ticket(2, status="ready-for-agent"),
        make_ticket(3, status="ready-for-agent"),
    ]
    # 2 existing claims in flight
    core = DispatchCore()
    snapshot = WorldSnapshot(
        tickets=tickets,
        claims={"claim/phase-1/10", "claim/phase-1/11"},
        concurrency_limit=2,
    )

    result = core.evaluate(snapshot)
    assert [t.number for t in result.frontier] == [1, 2, 3]
    # Concurrency limit reached, so 0 start actions
    assert len(result.actions) == 0


def test_concurrency_partially_filled():
    tickets = [
        make_ticket(1, status="ready-for-agent"),
        make_ticket(2, status="ready-for-agent"),
        make_ticket(3, status="ready-for-agent"),
    ]
    # 1 claim in flight, limit 2 -> starts 1 ticket
    core = DispatchCore()
    snapshot = WorldSnapshot(
        tickets=tickets,
        claims={"claim/phase-1/10"},
        open_prs=[],
        concurrency_limit=2,
    )

    result = core.evaluate(snapshot)
    assert [t.number for t in result.frontier] == [1, 2, 3]
    assert len(result.actions) == 1
    assert result.actions[0].ticket.number == 1


def test_classify_box_paused_by_developer_is_paused():
    now = datetime.datetime(2026, 10, 6, 12, 0, tzinfo=datetime.UTC)
    box = BoxStatus(
        checked_in_at=now,
        state=BoxState.paused_by_developer,
        current=None,
        paused_until=None,
    )
    assert classify_box(box, now, box_silent_hours=12) == "paused"
    assert classify_box(box, now, box_silent_hours=12, repo="owner/repo") == "paused"


def test_classify_box_not_ready_repo_is_paused_other_repo_available():
    now = datetime.datetime(2026, 10, 6, 12, 0, tzinfo=datetime.UTC)
    box = BoxStatus(
        checked_in_at=now,
        state=BoxState.working,
        current=None,
        paused_until=None,
        not_ready=(NotReady("owner/repo", NotReadyReason.baseline_red),),
    )
    # Repo in not_ready is paused (case-insensitive)
    assert classify_box(box, now, box_silent_hours=12, repo="owner/repo") == "paused"
    assert classify_box(box, now, box_silent_hours=12, repo="OWNER/REPO") == "paused"
    # Other repo is available
    assert classify_box(box, now, box_silent_hours=12, repo="owner/other") == "available"
    # No repo specified is available
    assert classify_box(box, now, box_silent_hours=12, repo=None) == "available"


def test_dispatch_starts_jules_on_a_not_ready_repo():
    now = datetime.datetime(2026, 10, 6, 12, 0, tzinfo=datetime.UTC)
    tickets = [make_ticket(1, status="ready-for-agent", runner="any")]
    box = BoxStatus(
        checked_in_at=now,
        state=BoxState.working,
        current=None,
        paused_until=None,
        not_ready=(NotReady("owner/target", NotReadyReason.baseline_red),),
    )
    snapshot = WorldSnapshot(
        tickets=tickets,
        box=box,
        repo_name="owner/target",
        now=now,
        concurrency_limit=2,
    )
    result = DispatchCore().evaluate(snapshot)
    assert result.box_state == "paused"
    start_actions = [a for a in result.actions if isinstance(a, StartTicketAction)]
    assert len(start_actions) >= 1
    assert start_actions[0].ticket.number == 1

