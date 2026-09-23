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
    Claim,
    DispatchCore,
    EscalatePRAction,
    OpenPR,
    PauseRepoAction,
    ReleaseClaimAction,
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


def test_scenario_second_vs_third_ci_failure():
    core = DispatchCore()
    ticket = make_ticket(1)

    # 1. Second failure on PR: dispatcher gives worker fix attempts, does NOT escalate
    pr_two_failures = OpenPR(
        number=101,
        branch="ticket/phase-1-01-feat",
        ticket_number=1,
        failed_ci_count=2,
    )
    snapshot_two = WorldSnapshot(
        tickets=[ticket],
        open_prs=[pr_two_failures],
        config=RepoConfig(concurrency=2),
    )
    result_two = core.evaluate(snapshot_two)
    escalate_actions_two = [a for a in result_two.actions if isinstance(a, EscalatePRAction)]
    assert len(escalate_actions_two) == 0

    # 2. Third failure on PR: dispatcher escalates
    pr_three_failures = OpenPR(
        number=101,
        branch="ticket/phase-1-01-feat",
        ticket_number=1,
        failed_ci_count=3,
        ci_log_excerpt="AssertionError: failed",
    )
    snapshot_three = WorldSnapshot(
        tickets=[ticket],
        open_prs=[pr_three_failures],
        config=RepoConfig(concurrency=2),
    )
    result_three = core.evaluate(snapshot_three)
    escalate_actions_three = [a for a in result_three.actions if isinstance(a, EscalatePRAction)]
    assert len(escalate_actions_three) == 1
    assert escalate_actions_three[0].pr_number == 101
    assert escalate_actions_three[0].ticket.number == 1


def test_scenario_two_escalations_in_and_outside_24_hours():
    core = DispatchCore()
    now = datetime.datetime(2026, 9, 22, 12, 0, 0, tzinfo=datetime.UTC)
    config = RepoConfig(circuit_breaker_escalations_limit=2, circuit_breaker_window_hours=24)
    ticket = make_ticket(1)

    # 1. Two escalations outside 24h (e.g. 26 hours ago and 25 hours ago): does NOT pause
    snapshot_outside = WorldSnapshot(
        tickets=[ticket],
        config=config,
        now=now,
        escalations=[
            now - datetime.timedelta(hours=26),
            now - datetime.timedelta(hours=25),
        ],
    )
    result_outside = core.evaluate(snapshot_outside)
    pause_actions_outside = [a for a in result_outside.actions if isinstance(a, PauseRepoAction)]
    assert len(pause_actions_outside) == 0
    # Eligible ticket can start
    assert len(result_outside.actions) == 1
    assert isinstance(result_outside.actions[0], StartTicketAction)

    # 2. Two escalations inside 24h (e.g. 10 hours ago and 1 hour ago): trips circuit breaker and pauses
    snapshot_inside = WorldSnapshot(
        tickets=[ticket],
        config=config,
        now=now,
        escalations=[
            now - datetime.timedelta(hours=10),
            now - datetime.timedelta(hours=1),
        ],
    )
    result_inside = core.evaluate(snapshot_inside)
    pause_actions_inside = [a for a in result_inside.actions if isinstance(a, PauseRepoAction)]
    assert len(pause_actions_inside) == 1
    # Paused repo starts nothing
    start_actions_inside = [a for a in result_inside.actions if isinstance(a, StartTicketAction)]
    assert len(start_actions_inside) == 0


def test_scenario_stale_claim_13_hour_quiet_released_and_11_hour_quiet_kept():
    core = DispatchCore()
    now = datetime.datetime(2026, 9, 22, 12, 0, 0, tzinfo=datetime.UTC)
    config = RepoConfig(stale_claim_hours=12)

    # Ticket 1 has claim with last commit 13 hours ago (stale)
    claim_13h = Claim(
        ref="claim/phase-1/01",
        ticket_number=1,
        effort="phase-1",
        last_commit_time=now - datetime.timedelta(hours=13),
        has_live_session=False,
    )
    # Ticket 2 has claim with last commit 11 hours ago (active, not stale)
    claim_11h = Claim(
        ref="claim/phase-1/02",
        ticket_number=2,
        effort="phase-1",
        last_commit_time=now - datetime.timedelta(hours=11),
        has_live_session=False,
    )
    # Ticket 3 has claim with last commit 15 hours ago BUT has a live Jules session -> NOT stale
    claim_with_live_session = Claim(
        ref="claim/phase-1/03",
        ticket_number=3,
        effort="phase-1",
        last_commit_time=now - datetime.timedelta(hours=15),
        has_live_session=True,
    )

    tickets = [make_ticket(1), make_ticket(2), make_ticket(3)]
    snapshot = WorldSnapshot(
        tickets=tickets,
        claims=[claim_13h, claim_11h, claim_with_live_session],
        config=config,
        now=now,
    )

    result = core.evaluate(snapshot)
    release_actions = [a for a in result.actions if isinstance(a, ReleaseClaimAction)]
    assert len(release_actions) == 1
    assert release_actions[0].ticket_number == 1
    assert release_actions[0].claim_ref == "claim/phase-1/01"


def test_scenario_held_or_escalated_blocker_keeps_dependents_off_frontier_independent_dispatched():
    core = DispatchCore()
    tickets = [
        make_ticket(1, status="blocked"),  # escalated ticket
        make_ticket(2, status="ready-for-agent", blocked_by=[1]),  # dependent on escalated
        make_ticket(3, status="ready-for-agent", blocked_by=[]),  # independent ticket
    ]
    snapshot = WorldSnapshot(
        tickets=tickets,
        config=RepoConfig(concurrency=2),
    )
    result = core.evaluate(snapshot)
    assert [t.number for t in result.frontier] == [3]
    assert len(result.actions) == 1
    assert result.actions[0].ticket.number == 3

