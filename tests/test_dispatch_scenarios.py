"""Scenario tests for live dispatch logic, quota reserve, daily cap, claim collision, and paused state.

WHY THIS EXISTS
---------------
Phase 1 Spec §Testing Decisions and Ticket 06 Acceptance Criteria 3, 4, 6, 8 mandate:
- Scenario tests: quota at the reserve boundary (stops a start at 91 sessions),
  daily cap reached, concurrency enforced from repo config, claim collision,
  and paused repo (`TICKET_ENGINE_PAUSED` set).
- Pure dispatch core: world snapshot in, actions out, zero I/O, zero clock reads.
"""
from __future__ import annotations

import datetime

from ticket_engine.config import RepoConfig
from ticket_engine.dispatch import (
    DispatchCore,
    StartTicketAction,
    WorldSnapshot,
)
from ticket_engine.parser import Ticket


def make_ticket(
    number: int,
    status: str = "ready-for-agent",
    blocked_by: list[int] | None = None,
    runner: str = "any",
    auto_merge: bool = True,
    effort: str = "phase-1",
) -> Ticket:
    return Ticket(
        number=number,
        title=f"Ticket {number}",
        slug=f"ticket-{number}",
        status=status,
        blocked_by=blocked_by or [],
        runner=runner,
        auto_merge=auto_merge,
        effort=effort,
    )


def test_scenario_quota_at_reserve_boundary_allows_start_at_90_stops_at_91():
    now = datetime.datetime(2026, 9, 22, 20, 0, 0, tzinfo=datetime.UTC)
    config = RepoConfig(jules_limit=100, jules_reserve=10, concurrency=2)
    core = DispatchCore()

    ticket = make_ticket(1)

    # 1. Exactly 90 sessions in last 24h: 10 remaining (equal to reserve 10) -> can start
    snapshot_90 = WorldSnapshot(
        tickets=[ticket],
        config=config,
        jules_sessions_count_24h=90,
        now=now,
    )
    result_90 = core.evaluate(snapshot_90)
    assert len(result_90.actions) == 1
    assert isinstance(result_90.actions[0], StartTicketAction)
    assert result_90.actions[0].ticket.number == 1

    # 2. Exactly 91 sessions in last 24h: 9 remaining (< reserve 10) -> stops start
    # Spec §Testing Decisions: "The reserve stops a start at 91 sessions."
    snapshot_91 = WorldSnapshot(
        tickets=[ticket],
        config=config,
        jules_sessions_count_24h=91,
        now=now,
    )
    result_91 = core.evaluate(snapshot_91)
    assert len(result_91.actions) == 0


def test_scenario_daily_cap_reached():
    config = RepoConfig(daily_cap=3, concurrency=2)
    core = DispatchCore()

    tickets = [make_ticket(1), make_ticket(2)]

    # Cap is 3. Repo already has 3 starts in last 24h -> 0 actions
    snapshot_cap_reached = WorldSnapshot(
        tickets=tickets,
        config=config,
        repo_starts_last_24h=3,
    )
    result_cap_reached = core.evaluate(snapshot_cap_reached)
    assert len(result_cap_reached.actions) == 0

    # Cap is 3. Repo has 2 starts in last 24h -> can start 1 ticket (since cap limit allows 1 more start)
    snapshot_cap_one_left = WorldSnapshot(
        tickets=tickets,
        config=config,
        repo_starts_last_24h=2,
    )
    result_cap_one_left = core.evaluate(snapshot_cap_one_left)
    assert len(result_cap_one_left.actions) == 1
    assert result_cap_one_left.actions[0].ticket.number == 1


def test_scenario_concurrency_limit_from_config():
    # Concurrency from repo config: 3
    config = RepoConfig(concurrency=3)
    core = DispatchCore()

    tickets = [make_ticket(1), make_ticket(2), make_ticket(3), make_ticket(4)]

    # 1 claim in flight -> can start 2
    snapshot = WorldSnapshot(
        tickets=tickets,
        claims={"claim/phase-1/99"},
        config=config,
    )
    result = core.evaluate(snapshot)
    assert len(result.actions) == 2
    assert [a.ticket.number for a in result.actions if isinstance(a, StartTicketAction)] == [1, 2]


def test_scenario_paused_repo_starts_nothing():
    config = RepoConfig(concurrency=2)
    core = DispatchCore()

    tickets = [make_ticket(1), make_ticket(2)]

    # TICKET_ENGINE_PAUSED set / paused=True
    snapshot_paused = WorldSnapshot(
        tickets=tickets,
        config=config,
        paused=True,
    )
    result = core.evaluate(snapshot_paused)
    assert [t.number for t in result.frontier] == [1, 2]
    # Starts nothing
    assert len(result.actions) == 0


def test_scenario_claim_collision_skips_claimed_ticket():
    core = DispatchCore()

    # Ticket 1 already has an active claim branch: claim/phase-1/01
    tickets = [make_ticket(1), make_ticket(2)]
    snapshot = WorldSnapshot(
        tickets=tickets,
        claims={"claim/phase-1/01"},
        config=RepoConfig(concurrency=2),
    )

    result = core.evaluate(snapshot)
    assert [t.number for t in result.frontier] == [1, 2]
    # Ticket 1 is skipped because it is already claimed; ticket 2 is started
    assert len(result.actions) == 1
    assert result.actions[0].ticket.number == 2
