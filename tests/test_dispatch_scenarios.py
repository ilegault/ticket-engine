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

from ticket_engine.box_status import BoxState, BoxStatus
from ticket_engine.config import RepoConfig, load_repo_config
from ticket_engine.dispatch import (
    NO_BOX,
    Claim,
    DispatchCore,
    EscalatePRAction,
    OpenPR,
    PauseRepoAction,
    ReleaseClaimAction,
    StartTicketAction,
    WorldSnapshot,
    claim_precheck,
    release_done_claims,
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


def test_config_loads_box_stale_claim_hours(tmp_path):
    config_file = tmp_path / ".ticket-engine.toml"
    config_file.write_text("box_stale_claim_hours = 6\n", encoding="utf-8")
    cfg = load_repo_config(tmp_path)
    assert cfg.box_stale_claim_hours == 6

    assert RepoConfig().box_stale_claim_hours == 8
    default_cfg = load_repo_config(tmp_path / "nonexistent")
    assert default_cfg.box_stale_claim_hours == 8


def test_claim_claimed_by_default():
    claim = Claim(ref="claim/phase-1/01", ticket_number=1)
    assert claim.claimed_by == ""


def test_scenario_box_stale_claim_evaluated():
    core = DispatchCore()
    now = datetime.datetime(2026, 9, 22, 12, 0, 0, tzinfo=datetime.UTC)
    config = RepoConfig(box_stale_claim_hours=8)

    # 1. Box claim at exactly now - 8h gives ReleaseClaimAction even with a matching live Jules session
    claim_8h = Claim(
        ref="claim/box-primary-worker/01",
        ticket_number=1,
        effort="box-primary-worker",
        claimed_by="box",
        last_commit_time=now - datetime.timedelta(hours=8),
    )
    # 2. Box claim at now - 7h59m is NOT released
    claim_7h59m = Claim(
        ref="claim/box-primary-worker/02",
        ticket_number=2,
        effort="box-primary-worker",
        claimed_by="box",
        last_commit_time=now - datetime.timedelta(hours=7, minutes=59),
    )
    # 3. Claims with last_commit_time=None are never released
    claim_none_box = Claim(
        ref="claim/box-primary-worker/03",
        ticket_number=3,
        effort="box-primary-worker",
        claimed_by="box",
        last_commit_time=None,
    )
    claim_none_jules = Claim(
        ref="claim/phase-1/04",
        ticket_number=4,
        effort="phase-1",
        claimed_by="jules",
        last_commit_time=None,
    )

    # Live Jules session matching ticket 1
    matching_session = {
        "id": "sessions/test1",
        "title": "box-primary-worker-01: First Ticket",
        "state": "RUNNING",
    }

    tickets = [make_ticket(1), make_ticket(2), make_ticket(3), make_ticket(4)]
    snapshot = WorldSnapshot(
        tickets=tickets,
        claims=[claim_8h, claim_7h59m, claim_none_box, claim_none_jules],
        jules_sessions=[matching_session],
        config=config,
        now=now,
    )

    result = core.evaluate(snapshot)
    releases = [a for a in result.actions if isinstance(a, ReleaseClaimAction)]
    assert len(releases) == 1
    assert releases[0].ticket_number == 1
    assert releases[0].claim_ref == "claim/box-primary-worker/01"
    assert releases[0].reason == "Stale box claim: no checkpoint in 8 hours"




def test_dispatch_box_available_leaves_frontier_for_box_and_starts_nothing():
    now = datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.UTC)
    box = BoxStatus(
        checked_in_at=now - datetime.timedelta(hours=1),
        state=BoxState.idle,
    )
    t1 = make_ticket(1, runner="any")
    t2 = make_ticket(2, runner="windows")
    t3 = Ticket(number=3, title="Lint fail", slug="lint-fail", status="ready-for-agent", raw_text="# 3: Lint fail\n\nNo criteria here")
    t4 = make_ticket(4, runner="any")
    claim4 = Claim(ref="claim/phase-1/04", ticket_number=4)

    snapshot = WorldSnapshot(
        tickets=[t1, t2, t3, t4],
        claims=[claim4],
        box=box,
        now=now,
    )
    core = DispatchCore()
    result = core.evaluate(snapshot)

    assert result.box_state == "available"
    assert not any(isinstance(a, StartTicketAction) for a in result.actions)
    assert result.left_for_box == [t1, t2]
    assert t3 in result.lint_held
    assert result.skipped_windows_tickets == [t2]


def test_dispatch_box_paused_quota_overflows_to_jules():
    now = datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.UTC)
    box = BoxStatus(
        checked_in_at=now - datetime.timedelta(hours=1),
        state=BoxState.paused_quota,
    )
    t1 = make_ticket(1, runner="any")
    t2 = make_ticket(2, runner="windows")
    snapshot = WorldSnapshot(tickets=[t1, t2], box=box, now=now)
    result = DispatchCore().evaluate(snapshot)

    assert result.box_state == "paused"
    assert result.left_for_box == []
    start_actions = [a for a in result.actions if isinstance(a, StartTicketAction)]
    assert len(start_actions) == 1
    assert start_actions[0].ticket.number == 1
    assert result.skipped_windows_tickets == [t2]


def test_jules_disabled_repo_gets_no_start_even_when_box_paused():
    now = datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.UTC)
    box = BoxStatus(
        checked_in_at=now - datetime.timedelta(hours=1),
        state=BoxState.paused_quota,
    )
    t1 = make_ticket(1, runner="any")

    cfg_disabled = RepoConfig(jules_enabled=False)
    snapshot_disabled = WorldSnapshot(tickets=[t1], box=box, now=now, config=cfg_disabled)
    result_disabled = DispatchCore().evaluate(snapshot_disabled)
    start_disabled = [a for a in result_disabled.actions if isinstance(a, StartTicketAction)]
    assert len(start_disabled) == 0
    assert result_disabled.left_for_box == []
    assert result_disabled.box_state == "paused"

    cfg_enabled = RepoConfig(jules_enabled=True)
    snapshot_enabled = WorldSnapshot(tickets=[t1], box=box, now=now, config=cfg_enabled)
    result_enabled = DispatchCore().evaluate(snapshot_enabled)
    start_enabled = [a for a in result_enabled.actions if isinstance(a, StartTicketAction)]
    assert len(start_enabled) >= 1


def test_dispatch_box_paused_weekly_cap_overflows_to_jules():
    now = datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.UTC)
    box = BoxStatus(
        checked_in_at=now - datetime.timedelta(hours=1),
        state=BoxState.paused_weekly_cap,
    )
    t1 = make_ticket(1, runner="any")
    snapshot = WorldSnapshot(tickets=[t1], box=box, now=now)
    result = DispatchCore().evaluate(snapshot)

    assert result.box_state == "paused"
    assert result.left_for_box == []
    start_actions = [a for a in result.actions if isinstance(a, StartTicketAction)]
    assert len(start_actions) == 1
    assert start_actions[0].ticket.number == 1


def test_dispatch_box_login_expired_overflows_to_jules():
    now = datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.UTC)
    box = BoxStatus(
        checked_in_at=now - datetime.timedelta(hours=1),
        state=BoxState.login_expired,
    )
    t1 = make_ticket(1, runner="any")
    snapshot = WorldSnapshot(tickets=[t1], box=box, now=now)
    result = DispatchCore().evaluate(snapshot)

    assert result.box_state == "paused"
    assert result.left_for_box == []
    start_actions = [a for a in result.actions if isinstance(a, StartTicketAction)]
    assert len(start_actions) == 1
    assert start_actions[0].ticket.number == 1


def test_dispatch_box_silent_when_checked_in_12_hours_ago():
    now = datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.UTC)
    box = BoxStatus(
        checked_in_at=now - datetime.timedelta(hours=12),
        state=BoxState.idle,
    )
    t1 = make_ticket(1, runner="any")
    t2 = make_ticket(2, runner="windows")
    snapshot = WorldSnapshot(tickets=[t1, t2], box=box, now=now)
    result = DispatchCore().evaluate(snapshot)

    assert result.box_state == "silent"
    assert result.left_for_box == []
    start_actions = [a for a in result.actions if isinstance(a, StartTicketAction)]
    assert len(start_actions) == 1
    assert start_actions[0].ticket.number == 1


def test_dispatch_box_unreadable_when_box_is_none():
    now = datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.UTC)
    t1 = make_ticket(1, runner="any")
    snapshot = WorldSnapshot(tickets=[t1], box=None, now=now)
    result = DispatchCore().evaluate(snapshot)

    assert result.box_state == "unreadable"
    assert result.left_for_box == []
    start_actions = [a for a in result.actions if isinstance(a, StartTicketAction)]
    assert len(start_actions) == 1
    assert start_actions[0].ticket.number == 1


def test_dispatch_box_none_when_default_no_box():
    now = datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.UTC)
    t1 = make_ticket(1, runner="any")
    snapshot = WorldSnapshot(tickets=[t1], now=now)
    result = DispatchCore().evaluate(snapshot)

    assert result.box_state == "none"
    assert result.left_for_box == []
    start_actions = [a for a in result.actions if isinstance(a, StartTicketAction)]
    assert len(start_actions) == 1
    assert start_actions[0].ticket.number == 1


def test_dispatch_windows_ticket_never_started_in_any_box_state():
    now = datetime.datetime(2026, 9, 26, 12, 0, 0, tzinfo=datetime.UTC)
    t_win = make_ticket(1, runner="windows")
    states = [
        BoxStatus(checked_in_at=now - datetime.timedelta(hours=1), state=BoxState.idle),
        BoxStatus(checked_in_at=now - datetime.timedelta(hours=1), state=BoxState.paused_quota),
        BoxStatus(checked_in_at=now - datetime.timedelta(hours=13), state=BoxState.working),
        None,
        NO_BOX,
    ]
    core = DispatchCore()
    for box in states:
        snapshot = WorldSnapshot(tickets=[t_win], box=box, now=now)
        result = core.evaluate(snapshot)
        assert not any(isinstance(a, StartTicketAction) for a in result.actions)
        assert result.skipped_windows_tickets == [t_win]


# ---------------------------------------------------------------------------
# claim_precheck (ticket 38): live-remote claimability check before claiming
# ---------------------------------------------------------------------------

def test_claim_precheck_unreadable_ticket_is_none():
    assert claim_precheck(None, []) == "unreadable"


def test_claim_precheck_already_done():
    t = make_ticket(38, status="done")
    assert claim_precheck(t, []) == "already-done"


def test_claim_precheck_already_claimed_by_claim_ref():
    t = make_ticket(38, status="ready-for-agent")
    assert claim_precheck(t, ["claim/box-primary-worker/38"]) == "already-claimed"


def test_claim_precheck_already_claimed_by_status_alone():
    t = make_ticket(38, status="in-progress")
    assert claim_precheck(t, []) == "already-claimed"


def test_claim_precheck_claimable():
    t = make_ticket(38, status="ready-for-agent")
    assert claim_precheck(t, []) == "claimable"


# ---------------------------------------------------------------------------
# release_done_claims (ticket 37): which done tickets' claims to release, and
# which to keep and why, before any Jules quota call.
# ---------------------------------------------------------------------------

REPO = "owner/repo"


def _live_session(effort: str, number: int, state: str = "RUNNING", repo: str = REPO) -> dict:
    return {
        "state": state,
        "title": f"{effort}-{number:02d}: Some title",
        "sourceContext": {"source": f"sources/github/{repo}"},
    }


def test_release_done_claims_released_with_no_sessions():
    tickets = [make_ticket(1, status="done")]
    released, kept = release_done_claims(
        tickets, ["claim/phase-1/01"], sessions=[], repo=REPO
    )
    assert kept == []
    assert len(released) == 1
    action = released[0]
    assert action.claim_ref == "claim/phase-1/01"
    assert action.ticket_number == 1
    assert action.effort == "phase-1"
    assert action.reason == "Ticket is done on the default branch"


def test_release_done_claims_kept_by_matching_live_session():
    tickets = [make_ticket(1, status="done")]
    sessions = [_live_session("phase-1", 1, state="RUNNING")]
    released, kept = release_done_claims(
        tickets, ["claim/phase-1/01"], sessions=sessions, repo=REPO
    )
    assert released == []
    assert kept == [
        (1, "claim/phase-1/01", "a live Jules session for this ticket is still open (RUNNING)")
    ]


def test_release_done_claims_released_when_live_session_is_another_repo():
    tickets = [make_ticket(1, status="done")]
    sessions = [_live_session("phase-1", 1, state="RUNNING", repo="other/repo")]
    released, kept = release_done_claims(
        tickets, ["claim/phase-1/01"], sessions=sessions, repo=REPO
    )
    assert kept == []
    assert len(released) == 1
    assert released[0].ticket_number == 1


def test_release_done_claims_released_when_live_session_is_another_effort():
    tickets = [make_ticket(1, status="done", effort="phase-1")]
    sessions = [_live_session("other-effort", 1, state="RUNNING")]
    released, kept = release_done_claims(
        tickets, ["claim/phase-1/01"], sessions=sessions, repo=REPO
    )
    assert kept == []
    assert len(released) == 1
    assert released[0].ticket_number == 1


def test_release_done_claims_kept_for_every_done_claim_when_sessions_is_none():
    tickets = [make_ticket(1, status="done"), make_ticket(2, status="done")]
    released, kept = release_done_claims(
        tickets,
        ["claim/phase-1/01", "claim/phase-1/02"],
        sessions=None,
        repo=REPO,
    )
    assert released == []
    assert kept == [
        (1, "claim/phase-1/01", "Jules sessions could not be listed, so the claim was not released blind"),
        (2, "claim/phase-1/02", "Jules sessions could not be listed, so the claim was not released blind"),
    ]


def test_release_done_claims_ignores_claim_for_ticket_not_done():
    tickets = [make_ticket(1, status="in-progress")]
    released, kept = release_done_claims(
        tickets, ["claim/phase-1/01"], sessions=[], repo=REPO
    )
    assert released == []
    assert kept == []
