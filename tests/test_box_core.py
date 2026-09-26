"""Tests for ticket_engine.box_core.

Covers:
- Types: BoxWorld, BoxRepo, BoxPR, and frozen dataclass step types.
- Precedence: individual rule tests and adjacent-pair tests proving precedence.
- Every runner and frontier definition: windows tickets, any tickets, blockers, claims, lint-held.
- Quota timeline: after_quota_error, after_success, asserting at 0h, 1h, 5h01m, 17h01m.
- Purity: no forbidden imports, no clock reads, WHY THIS EXISTS docstring.
"""
from __future__ import annotations

import datetime
import pathlib

import pytest

from ticket_engine.box_core import (
    BoxCore,
    BoxPR,
    BoxRepo,
    BoxWorld,
    ClaimTicket,
    CloseAlert,
    FixCI,
    RaiseAlert,
    ResumeClaim,
    Wait,
    WriteStatus,
)
from ticket_engine.box_status import AlertKind
from ticket_engine.config import RepoConfig
from ticket_engine.parser import Ticket, TicketParser


def ticket(
    number: int,
    status: str,
    blocked_by: str = "None",
    effort: str = "effort-a",
    title: str | None = None,
    runner: str = "any",
    raw_extra: str = "",
) -> Ticket:
    """Parse a real ticket file body, the same way the dispatcher reads the repo."""
    title = title or f"Ticket {number}"
    text = (
        f"# {number}: {title}\n\n"
        f"**Status:** {status}\n\n"
        f"**Blocked by:** {blocked_by}\n\n"
        f"**Runner:** {runner}\n\n"
        f"## Acceptance criteria\n\n- [ ] criterion 1\n\n"
        f"{raw_extra}\n"
    )
    path = pathlib.Path(".scratch") / effort / "issues" / f"{number:02d}-t.md"
    return TicketParser().parse_text(text, filename=path.name, path=path)


def make_world(
    now: datetime.datetime,
    repos: list[BoxRepo] | None = None,
    starts_24h: dict[str, int] | None = None,
    concurrency: int = 1,
    quota_first_failure: datetime.datetime | None = None,
    quota_retry_at: datetime.datetime | None = None,
    weekly_cap_alert_open: bool = False,
    last_status_write: datetime.datetime | None = None,
    status_interval_minutes: int = 30,
    poll_interval_minutes: int = 10,
    weekly_cap_after_hours: int = 5,
    weekly_cap_backoff_hours: int = 12,
) -> BoxWorld:
    return BoxWorld(
        repos=repos if repos is not None else [],
        starts_24h=starts_24h if starts_24h is not None else {},
        concurrency=concurrency,
        quota_first_failure=quota_first_failure,
        quota_retry_at=quota_retry_at,
        weekly_cap_alert_open=weekly_cap_alert_open,
        last_status_write=last_status_write,
        status_interval_minutes=status_interval_minutes,
        poll_interval_minutes=poll_interval_minutes,
        weekly_cap_after_hours=weekly_cap_after_hours,
        weekly_cap_backoff_hours=weekly_cap_backoff_hours,
        now=now,
    )


# ---------------------------------------------------------------------------
# Criterion 1: Types
# ---------------------------------------------------------------------------


def test_types_and_fields() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 0, tzinfo=datetime.UTC)
    t = ticket(1, "ready-for-agent")
    pr = BoxPR(ticket_number=1, pr_number=101, ci_failed=True, fix_attempts=0)
    assert pr.ticket_number == 1
    assert pr.pr_number == 101
    assert pr.ci_failed is True
    assert pr.fix_attempts == 0

    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t],
        config=RepoConfig(),
        paused=False,
        claims={1: "box"},
        open_prs=[pr],
    )
    assert repo.repo == "org/repo-a"
    assert repo.claims[1] == "box"
    assert repo.open_prs == [pr]

    world = make_world(
        now=now,
        repos=[repo],
        starts_24h={"org/repo-a": 2},
        concurrency=1,
        quota_first_failure=now,
        quota_retry_at=now + datetime.timedelta(hours=1),
        weekly_cap_alert_open=False,
        last_status_write=now,
    )
    assert world.repos == [repo]
    assert world.starts_24h == {"org/repo-a": 2}
    assert world.concurrency == 1
    assert world.now == now

    # Step types are frozen dataclasses
    ws = WriteStatus()
    wait = Wait(until=now)
    fix = FixCI(repo="org/repo-a", ticket_number=1, pr_number=101)
    res = ResumeClaim(repo="org/repo-a", ticket_number=1)
    claim = ClaimTicket(repo="org/repo-a", ticket=t)
    raise_alt = RaiseAlert(kind=AlertKind.weekly_cap)
    close_alt = CloseAlert(kind=AlertKind.weekly_cap)

    for step in (ws, wait, fix, res, claim, raise_alt, close_alt):
        with pytest.raises((AttributeError, TypeError)):
            step.anything = "mutate"  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Criterion 2: Precedence (one per rule, one per adjacent pair)
# ---------------------------------------------------------------------------


def test_rule_1_write_status_when_none() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 0, tzinfo=datetime.UTC)
    world = make_world(now=now, last_status_write=None)
    step = BoxCore.next_step(world)
    assert step == WriteStatus()


def test_rule_1_write_status_when_older_than_interval() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 35, tzinfo=datetime.UTC)
    last = datetime.datetime(2026, 9, 26, 12, 0, tzinfo=datetime.UTC)
    world = make_world(now=now, last_status_write=last, status_interval_minutes=30)
    step = BoxCore.next_step(world)
    assert step == WriteStatus()


def test_rule_2_wait_quota_retry_at() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    retry = datetime.datetime(2026, 9, 26, 13, 0, tzinfo=datetime.UTC)
    # last_status_write is fresh (10 mins ago < 30 min interval)
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        quota_retry_at=retry,
    )
    step = BoxCore.next_step(world)
    assert step == Wait(until=retry)


def test_rule_3_fix_ci() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    t = ticket(1, "ready-for-agent")
    pr = BoxPR(ticket_number=1, pr_number=101, ci_failed=True, fix_attempts=1)
    cfg = RepoConfig(max_fix_attempts=3)
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t],
        config=cfg,
        paused=False,
        claims={1: "box"},
        open_prs=[pr],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
    )
    step = BoxCore.next_step(world)
    assert step == FixCI(repo="org/repo-a", ticket_number=1, pr_number=101)


def test_rule_4_resume_claim() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    t = ticket(1, "ready-for-agent")
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t],
        config=RepoConfig(),
        paused=False,
        claims={1: "box"},
        open_prs=[],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
    )
    step = BoxCore.next_step(world)
    assert step == ResumeClaim(repo="org/repo-a", ticket_number=1)


def test_rule_5_claim_ticket() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    t = ticket(1, "ready-for-agent")
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t],
        config=RepoConfig(daily_cap=5),
        paused=False,
        claims={},
        open_prs=[],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
        starts_24h={"org/repo-a": 1},
        concurrency=1,
    )
    step = BoxCore.next_step(world)
    assert step == ClaimTicket(repo="org/repo-a", ticket=t)


def test_rule_6_wait_poll_interval() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[],
        config=RepoConfig(),
        paused=False,
        claims={},
        open_prs=[],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
        poll_interval_minutes=10,
    )
    step = BoxCore.next_step(world)
    assert step == Wait(until=now + datetime.timedelta(minutes=10))


# --- Adjacent pairs proving higher rule wins ---


def test_precedence_rule_1_beats_rule_2() -> None:
    # last_status_write is None AND quota_retry_at is in future -> WriteStatus wins
    now = datetime.datetime(2026, 9, 26, 12, 0, tzinfo=datetime.UTC)
    retry = now + datetime.timedelta(hours=1)
    world = make_world(now=now, last_status_write=None, quota_retry_at=retry)
    assert BoxCore.next_step(world) == WriteStatus()


def test_precedence_rule_2_beats_rule_3() -> None:
    # Quota paused AND a PR has ci_failed with attempts remaining -> Wait(retry) wins
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    retry = now + datetime.timedelta(minutes=30)
    t = ticket(1, "ready-for-agent")
    pr = BoxPR(ticket_number=1, pr_number=101, ci_failed=True, fix_attempts=0)
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t],
        config=RepoConfig(max_fix_attempts=3),
        paused=False,
        claims={1: "box"},
        open_prs=[pr],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        quota_retry_at=retry,
        repos=[repo],
    )
    assert BoxCore.next_step(world) == Wait(until=retry)


def test_precedence_rule_3_beats_rule_4() -> None:
    # Both a red PR and another box-claimed ticket without PR -> FixCI wins
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    t1 = ticket(1, "ready-for-agent")
    t2 = ticket(2, "ready-for-agent")
    pr1 = BoxPR(ticket_number=1, pr_number=101, ci_failed=True, fix_attempts=0)
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t1, t2],
        config=RepoConfig(max_fix_attempts=3),
        paused=False,
        claims={1: "box", 2: "box"},
        open_prs=[pr1],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
    )
    assert BoxCore.next_step(world) == FixCI(repo="org/repo-a", ticket_number=1, pr_number=101)


def test_precedence_rule_4_beats_rule_5() -> None:
    # A box-claimed unfinished ticket with no PR AND an unclaimed frontier ticket -> ResumeClaim wins
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    t1 = ticket(1, "ready-for-agent")
    t2 = ticket(2, "ready-for-agent")
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t1, t2],
        config=RepoConfig(),
        paused=False,
        claims={1: "box"},
        open_prs=[],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
        concurrency=2,
    )
    assert BoxCore.next_step(world) == ResumeClaim(repo="org/repo-a", ticket_number=1)


def test_precedence_rule_5_beats_rule_6() -> None:
    # Eligible frontier ticket vs fallback wait -> ClaimTicket wins
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    t = ticket(1, "ready-for-agent")
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t],
        config=RepoConfig(),
        paused=False,
        claims={},
        open_prs=[],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
    )
    assert BoxCore.next_step(world) == ClaimTicket(repo="org/repo-a", ticket=t)


# ---------------------------------------------------------------------------
# Criterion 3: Every runner, one frontier definition
# ---------------------------------------------------------------------------


def test_runner_windows_is_claimable() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    t_win = ticket(1, "ready-for-agent", runner="windows")
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t_win],
        config=RepoConfig(),
        paused=False,
        claims={},
        open_prs=[],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
    )
    step = BoxCore.next_step(world)
    assert step == ClaimTicket(repo="org/repo-a", ticket=t_win)


def test_runner_any_is_claimable() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    t_any = ticket(1, "ready-for-agent", runner="any")
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t_any],
        config=RepoConfig(),
        paused=False,
        claims={},
        open_prs=[],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
    )
    step = BoxCore.next_step(world)
    assert step == ClaimTicket(repo="org/repo-a", ticket=t_any)


def test_frontier_respects_blockers() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    # Ticket 2 is ready-for-agent, but blocked by ticket 1 which is in-progress
    t1 = ticket(1, "in-progress")
    t2 = ticket(2, "ready-for-agent", blocked_by="1")
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t1, t2],
        config=RepoConfig(),
        paused=False,
        claims={},
        open_prs=[],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
        poll_interval_minutes=10,
    )
    # Ticket 2 is NOT on frontier because blocker 1 is not done -> Wait, not ClaimTicket
    step = BoxCore.next_step(world)
    assert step == Wait(until=now + datetime.timedelta(minutes=10))


def test_claimed_ticket_never_claimed_again() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    t1 = ticket(1, "ready-for-agent")
    t2 = ticket(2, "ready-for-agent")
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t1, t2],
        config=RepoConfig(),
        paused=False,
        # 1 claimed by jules; 2 is unclaimed
        claims={1: "jules"},
        open_prs=[],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
    )
    step = BoxCore.next_step(world)
    # 1 is skipped because claimed; 2 is claimed
    assert step == ClaimTicket(repo="org/repo-a", ticket=t2)


def test_lint_held_ticket_skipped_for_next() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    # Ticket 1 has unauthorised test deletion text -> lint findings
    t1 = ticket(1, "ready-for-agent", raw_extra="Please delete `test_unauthorised_removal`.")
    t2 = ticket(2, "ready-for-agent")
    repo = BoxRepo(
        repo="org/repo-a",
        tickets=[t1, t2],
        config=RepoConfig(),
        paused=False,
        claims={},
        open_prs=[],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[repo],
    )
    step = BoxCore.next_step(world)
    # 1 is skipped for lint findings; 2 is claimed
    assert step == ClaimTicket(repo="org/repo-a", ticket=t2)


def test_concurrency_and_daily_cap_and_paused_repo() -> None:
    now = datetime.datetime(2026, 9, 26, 12, 10, tzinfo=datetime.UTC)
    t1 = ticket(1, "ready-for-agent")
    t2 = ticket(2, "ready-for-agent")

    # Repo paused -> skipped
    r_paused = BoxRepo(
        repo="org/paused",
        tickets=[t1],
        config=RepoConfig(),
        paused=True,
        claims={},
        open_prs=[],
    )
    # Repo at daily cap -> skipped
    r_cap = BoxRepo(
        repo="org/capped",
        tickets=[t2],
        config=RepoConfig(daily_cap=1),
        paused=False,
        claims={},
        open_prs=[],
    )
    world = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[r_paused, r_cap],
        starts_24h={"org/capped": 1},
        poll_interval_minutes=10,
    )
    assert BoxCore.next_step(world) == Wait(until=now + datetime.timedelta(minutes=10))

    # Concurrency reached: 1 box claim unfinished, concurrency=1 -> cannot claim
    t3 = ticket(3, "ready-for-agent")
    t4 = ticket(4, "ready-for-agent")
    pr3 = BoxPR(ticket_number=3, pr_number=103, ci_failed=False, fix_attempts=0)
    r_concur = BoxRepo(
        repo="org/active",
        tickets=[t3, t4],
        config=RepoConfig(),
        paused=False,
        claims={3: "box"},
        open_prs=[pr3],
    )
    world2 = make_world(
        now=now,
        last_status_write=now - datetime.timedelta(minutes=10),
        repos=[r_concur],
        concurrency=1,
        poll_interval_minutes=10,
    )
    # t3 is claimed by box and unfinished (PR open), so unfinished_box_claims == 1 >= concurrency
    assert BoxCore.next_step(world2) == Wait(until=now + datetime.timedelta(minutes=10))


# ---------------------------------------------------------------------------
# Criterion 4: The quota timeline
# ---------------------------------------------------------------------------


def test_quota_timeline_at_0h_1h_5h01m_17h01m() -> None:
    t0 = datetime.datetime(2026, 9, 26, 0, 0, tzinfo=datetime.UTC)

    # 1. At 0h: first failure
    world_0h = make_world(
        now=t0,
        quota_first_failure=None,
        weekly_cap_alert_open=False,
    )
    first, retry, alerts = BoxCore.after_quota_error(world_0h)
    assert first == t0
    assert retry == t0 + datetime.timedelta(hours=1)
    assert alerts == []

    # If reset_at is given at 0h, retry is reset_at + 60s
    reset_at = t0 + datetime.timedelta(minutes=45)
    first_r, retry_r, alerts_r = BoxCore.after_quota_error(world_0h, reset_at=reset_at)
    assert first_r == t0
    assert retry_r == reset_at + datetime.timedelta(seconds=60)
    assert alerts_r == []

    # 2. At 1h: subsequent failure within 5 hours
    t1 = t0 + datetime.timedelta(hours=1)
    world_1h = make_world(
        now=t1,
        quota_first_failure=t0,
        weekly_cap_alert_open=False,
    )
    first, retry, alerts = BoxCore.after_quota_error(world_1h)
    assert first == t0
    assert retry == t1 + datetime.timedelta(hours=1)
    assert alerts == []

    # 3. At 5h 01m: more than weekly_cap_after_hours (5h) after first failure
    t_5h01m = t0 + datetime.timedelta(hours=5, minutes=1)
    world_5h01m = make_world(
        now=t_5h01m,
        quota_first_failure=t0,
        weekly_cap_alert_open=False,
    )
    first, retry, alerts = BoxCore.after_quota_error(world_5h01m)
    assert first == t0
    assert retry == t_5h01m + datetime.timedelta(hours=12)
    assert alerts == [RaiseAlert(kind=AlertKind.weekly_cap)]

    # 4. At 17h 01m: subsequent failure after weekly cap already alerted (alert open)
    t_17h01m = t0 + datetime.timedelta(hours=17, minutes=1)
    world_17h01m = make_world(
        now=t_17h01m,
        quota_first_failure=t0,
        weekly_cap_alert_open=True,
    )
    first, retry, alerts = BoxCore.after_quota_error(world_17h01m)
    assert first == t0
    assert retry == t_17h01m + datetime.timedelta(hours=12)
    assert alerts == []

    # 5. after_success closes alert when open, does nothing when not open
    first_s, retry_s, alerts_s = BoxCore.after_success(world_17h01m)
    assert first_s is None
    assert retry_s is None
    assert alerts_s == [CloseAlert(kind=AlertKind.weekly_cap)]

    first_s2, retry_s2, alerts_s2 = BoxCore.after_success(world_0h)
    assert first_s2 is None
    assert retry_s2 is None
    assert alerts_s2 == []


# ---------------------------------------------------------------------------
# Criterion 5: Purity
# ---------------------------------------------------------------------------


def test_purity() -> None:
    import inspect

    import ticket_engine.box_core as box_core_mod

    source = inspect.getsource(box_core_mod)

    # Asserts module docstring has WHY THIS EXISTS
    doc = box_core_mod.__doc__ or ""
    assert "WHY THIS EXISTS" in doc
    assert "snapshot-testable" in doc.lower() or "pure" in doc.lower()

    # Asserts datetime.now( does not appear in source
    assert "datetime.now(" not in source

    # Asserts none of forbidden modules appear in import lines
    forbidden = ["github", "jules", "agy", "local_worker", "subprocess", "urllib", "time"]
    for line in source.splitlines():
        line_clean = line.strip()
        if line_clean.startswith(("import ", "from ")):
            for bad in forbidden:
                # check as word/token in import line
                parts = line_clean.replace(",", " ").split()
                assert bad not in parts, f"Forbidden import '{bad}' found in: {line_clean}"
