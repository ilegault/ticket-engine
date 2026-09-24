"""Tests for how the dispatcher reads Jules sessions.

WHY THIS EXISTS
---------------
1. The dispatcher only counted made-up state names (RUNNING, ACTIVE, PENDING) as
   live, so a Jules session that was PLANNING or waiting on a question looked dead
   and its claim could be released under it. Every non-terminal Jules state must
   count as live; COMPLETED and FAILED must not.
2. The per-repo daily cap counted every session in the Jules list (any repo, any
   age), so once ten sessions existed anywhere, the repo could never start another
   ticket and the merge -> dispatch cycle stopped. Only this repo's sessions from
   the last 24 hours may count.
"""
from __future__ import annotations

import datetime
from unittest.mock import MagicMock

import pytest

from ticket_engine.config import RepoConfig
from ticket_engine.dispatch import (
    Claim,
    DispatchCore,
    ReleaseClaimAction,
    WorldSnapshot,
    count_repo_starts,
    is_live_session_state,
)
from ticket_engine.live_dispatch import LiveDispatcher
from ticket_engine.parser import Ticket

REPO = "owner/Slackbot"
NOW = datetime.datetime(2026, 9, 24, 12, 0, tzinfo=datetime.UTC)


def make_ticket(number: int, status: str = "ready-for-agent") -> Ticket:
    return Ticket(
        number=number,
        title=f"Ticket {number}",
        slug=f"ticket-{number}",
        status=status,
        runner="any",
        effort="phase-1",
        blocked_by=[],
    )


def session(state: str = "COMPLETED", number: int = 40, repo: str = REPO, hours_ago: float = 1) -> dict:
    created = NOW - datetime.timedelta(hours=hours_ago)
    return {
        "name": f"sessions/{number}{hours_ago}",
        "state": state,
        "title": f"phase-1-{number:02d}: Some ticket",
        "sourceContext": {"source": f"sources/github/{repo}"},
        "createTime": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


@pytest.mark.parametrize(
    "state",
    [
        "QUEUED",
        "PLANNING",
        "AWAITING_PLAN_APPROVAL",
        "AWAITING_USER_FEEDBACK",
        "IN_PROGRESS",
        "PAUSED",
        "awaiting_user_feedback",
    ],
)
def test_every_non_terminal_jules_state_is_live(state):
    assert is_live_session_state(state) is True


@pytest.mark.parametrize("state", ["COMPLETED", "FAILED", "", None, "STATE_UNSPECIFIED"])
def test_terminal_and_unknown_states_are_not_live(state):
    assert is_live_session_state(state) is False


def _stale_claim_snapshot(state: str) -> WorldSnapshot:
    claim = Claim(
        ref="claim/phase-1/40",
        ticket_number=40,
        last_commit_time=NOW - datetime.timedelta(hours=30),
    )
    return WorldSnapshot(
        tickets=[make_ticket(40, status="in-progress")],
        claims=[claim],
        jules_sessions=[session(state)],
        now=NOW,
    )


def test_waiting_session_keeps_its_claim_from_being_released_as_stale():
    result = DispatchCore().evaluate(_stale_claim_snapshot("AWAITING_USER_FEEDBACK"))
    assert not [a for a in result.actions if isinstance(a, ReleaseClaimAction)]


def test_finished_session_does_not_protect_a_stale_claim():
    result = DispatchCore().evaluate(_stale_claim_snapshot("COMPLETED"))
    released = [a.claim_ref for a in result.actions if isinstance(a, ReleaseClaimAction)]
    assert released == ["claim/phase-1/40"]


def test_live_dispatch_keeps_claim_for_done_ticket_while_its_session_is_waiting():
    github = MagicMock()
    github.get_default_branch_sha.return_value = "base123sha"
    github.list_claim_branches.return_value = ["claim/phase-1/01"]
    github.get_repo_variable.return_value = None
    jules = MagicMock()
    jules.count_recent_sessions.return_value = 0
    jules.list_sessions.return_value = [session("AWAITING_USER_FEEDBACK", number=1)]

    dispatcher = LiveDispatcher(
        repo=REPO, github_client=github, jules_client=jules, config=RepoConfig(), skill_text="# s"
    )
    dispatcher.dispatch(tickets=[make_ticket(1, status="done")])

    github.delete_branch.assert_not_called()


# --- per-repo daily start count -------------------------------------------


def test_count_repo_starts_counts_only_this_repo_in_the_window():
    sessions = [
        session(repo=REPO, hours_ago=1),
        session(repo=REPO, hours_ago=23),
        session(repo=REPO, hours_ago=25),  # too old
        session(repo="owner/TDS-T8", hours_ago=1),  # other repo
        session(repo="owner/Slackbot-archive", hours_ago=1),  # prefix, not the repo
    ]
    assert count_repo_starts(sessions, repo=REPO, now=NOW, hours=24) == 2


def test_count_repo_starts_ignores_sessions_without_a_usable_timestamp():
    bad = session()
    bad["createTime"] = "not-a-time"
    missing = session()
    del missing["createTime"]
    assert count_repo_starts([bad, missing], repo=REPO, now=NOW, hours=24) == 0


def test_many_old_and_other_repo_sessions_do_not_hit_the_daily_cap():
    # Twelve finished sessions: some for other repos, the rest days old. Under the
    # old count this was 12 >= daily_cap 10 and nothing ever started again.
    old = [session(repo=REPO, number=n, hours_ago=48) for n in range(1, 7)]
    other = [session(repo="owner/TDS-T8", number=n, hours_ago=1) for n in range(1, 7)]
    github = MagicMock()
    github.get_default_branch_sha.return_value = "base123sha"
    github.list_claim_branches.return_value = []
    github.create_claim_branch.return_value = True
    github.get_repo_variable.return_value = None
    jules = MagicMock()
    jules.count_recent_sessions.return_value = 12
    jules.list_sessions.return_value = old + other

    dispatcher = LiveDispatcher(
        repo=REPO,
        github_client=github,
        jules_client=jules,
        config=RepoConfig(daily_cap=10),
        skill_text="# s",
    )
    started = dispatcher.dispatch(tickets=[make_ticket(41)])

    assert [t.number for t in started] == [41]


def test_daily_cap_still_stops_starts_once_reached():
    recent = [session(repo=REPO, number=n, hours_ago=0.5) for n in range(1, 4)]
    github = MagicMock()
    github.get_default_branch_sha.return_value = "base123sha"
    github.list_claim_branches.return_value = []
    github.create_claim_branch.return_value = True
    github.get_repo_variable.return_value = None
    jules = MagicMock()
    jules.count_recent_sessions.return_value = 3
    jules.list_sessions.return_value = recent

    dispatcher = LiveDispatcher(
        repo=REPO,
        github_client=github,
        jules_client=jules,
        config=RepoConfig(daily_cap=3),
        skill_text="# s",
    )

    assert dispatcher.dispatch(tickets=[make_ticket(41)]) == []
