"""Pure core deciding the box worker's next step and quota timeline.

WHY THIS EXISTS
---------------
Phase 2 (ADR 0006) introduces the box: a Windows mini PC running the local
worker permanently to work tickets overnight. While the box may keep local
state across ticks (its start ledger and quota-pause file), all decisions
about what action to take next are pure functions over a snapshot (BoxWorld).
Keeping BoxCore pure ensures every scheduling precedence rule, daily cap,
concurrency limit, and quota retry backoff is completely snapshot-testable
without clock reads, network I/O, or subprocess spawning.

Ticket 58 (ADR 0009 rule 4; ADR 0010 rules 3, 6, 7): delisted repos retain
their unfinished work (rules 3, 4) but receive no new claims (rule 5 skips
`accepting_new=False`), and `developer_paused` pauses new claims while
permitting in-flight work to resume.

Ticket 70 (spec §Problem Statement, ADR 0011 rule 1): fix attempts are counted
durably in `fix_attempts.json`, and running out escalates the ticket via
`EscalateFixes` rather than stalling.

Ticket 80 (ADR 0012): the box updates its own engine checkout between runs.
`BoxWorld` carries the engine's default-branch head, the commit the process is
running and the commit the launcher rolled back; `UpdateEngine` is chosen right
after `WriteStatus`, never onto a rolled-back commit.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass

from ticket_engine.box_status import AlertKind
from ticket_engine.config import RepoConfig
from ticket_engine.dispatch import DispatchCore, WorldSnapshot
from ticket_engine.parser import Ticket
from ticket_engine.ticket_lint import lint_ticket

__all__ = [
    "BoxCore",
    "BoxPR",
    "BoxRepo",
    "BoxStep",
    "BoxWorld",
    "ClaimTicket",
    "CloseAlert",
    "EscalateFixes",
    "FixCI",
    "RaiseAlert",
    "ResumeClaim",
    "UpdateEngine",
    "Wait",
    "WriteStatus",
]


@dataclass(frozen=True)
class BoxPR:
    """An open pull request created by the box worker."""

    ticket_number: int
    pr_number: int
    ci_failed: bool
    fix_attempts: int
    escalated: bool = False


@dataclass(frozen=True)
class BoxRepo:
    """State snapshot of a target repository configured for the box."""

    repo: str
    tickets: list[Ticket]
    config: RepoConfig
    paused: bool
    claims: dict[int, str]
    open_prs: list[BoxPR]
    accepting_new: bool = True


@dataclass(frozen=True)
class BoxWorld:
    """Complete snapshot of the box's world across all configured repos."""

    repos: list[BoxRepo]
    starts_24h: dict[str, int]
    concurrency: int
    quota_first_failure: datetime.datetime | None
    quota_retry_at: datetime.datetime | None
    weekly_cap_alert_open: bool
    last_status_write: datetime.datetime | None
    status_interval_minutes: int
    poll_interval_minutes: int
    weekly_cap_after_hours: int
    weekly_cap_backoff_hours: int
    now: datetime.datetime
    developer_paused: bool = False
    engine_head: str = ""
    running_commit: str = ""
    bad_commit: str = ""


@dataclass(frozen=True)
class WriteStatus:
    """Action to update the pinned box status issue."""


@dataclass(frozen=True)
class UpdateEngine:
    """Action to move the box's engine checkout to `commit` and restart."""

    commit: str


@dataclass(frozen=True)
class Wait:
    """Action to sleep until the given timestamp."""

    until: datetime.datetime


@dataclass(frozen=True)
class FixCI:
    """Action to run CI-fixer on a box PR with red CI."""

    repo: str
    ticket_number: int
    pr_number: int


@dataclass(frozen=True)
class EscalateFixes:
    """Action to escalate a box PR whose fix attempts are exhausted."""

    repo: str
    ticket_number: int
    pr_number: int


@dataclass(frozen=True)
class ResumeClaim:
    """Action to resume working on an unfinished claimed ticket."""

    repo: str
    ticket_number: int


@dataclass(frozen=True)
class ClaimTicket:
    """Action to claim and begin working on a frontier ticket."""

    repo: str
    ticket: Ticket


@dataclass(frozen=True)
class RaiseAlert:
    """Action to open an operational alert issue."""

    kind: AlertKind


@dataclass(frozen=True)
class CloseAlert:
    """Action to close an active operational alert issue."""

    kind: AlertKind


type BoxStep = (
    WriteStatus
    | UpdateEngine
    | Wait
    | FixCI
    | EscalateFixes
    | ResumeClaim
    | ClaimTicket
    | RaiseAlert
    | CloseAlert
)


class BoxCore:
    """Pure scheduling and quota logic for the box worker."""

    def __init__(self, dispatch_core: DispatchCore | None = None) -> None:
        self._dispatch_core = dispatch_core or DispatchCore()

    def _next_step(self, world: BoxWorld) -> BoxStep:
        # Rule 1: WriteStatus when last_status_write is None or older than status_interval_minutes
        if (
            world.last_status_write is None
            or (world.now - world.last_status_write)
            >= datetime.timedelta(minutes=world.status_interval_minutes)
        ):
            return WriteStatus()

        # Rule 1b (ticket 80, ADR 0012): the engine's default branch moved and the
        # launcher has not rolled back to it. Each tick is between runs.
        if (
            world.engine_head
            and world.running_commit
            and world.engine_head != world.running_commit
            and world.engine_head != world.bad_commit
           
        ):
            return UpdateEngine(commit=world.engine_head)

        # Rule 2: Wait(quota_retry_at) while now < quota_retry_at
        if world.quota_retry_at is not None and world.now < world.quota_retry_at:
            return Wait(until=world.quota_retry_at)

        # Rule 3: For a box-claimed ticket whose open PR has ci_failed and is not escalated,
        # return FixCI while fix_attempts < config.max_fix_attempts, otherwise EscalateFixes.
        for repo in world.repos:
            for pr in sorted(repo.open_prs, key=lambda p: p.ticket_number):
                if (
                    repo.claims.get(pr.ticket_number) == "box"
                    and pr.ci_failed
                    and not pr.escalated
                ):
                    if pr.fix_attempts < repo.config.max_fix_attempts:
                        return FixCI(
                            repo=repo.repo,
                            ticket_number=pr.ticket_number,
                            pr_number=pr.pr_number,
                        )
                    return EscalateFixes(
                        repo=repo.repo,
                        ticket_number=pr.ticket_number,
                        pr_number=pr.pr_number,
                    )

        # Rule 4: ResumeClaim for a box-claimed ticket that is not done and has no open PR
        for repo in world.repos:
            open_pr_ticket_numbers = {pr.ticket_number for pr in repo.open_prs}
            tickets_by_number = {t.number: t for t in repo.tickets}
            box_claimed_numbers = sorted(
                num
                for num, claimant in repo.claims.items()
                if claimant == "box" and num not in open_pr_ticket_numbers
            )
            for num in box_claimed_numbers:
                t = tickets_by_number.get(num)
                if t is None or not t.is_done():
                    return ResumeClaim(repo=repo.repo, ticket_number=num)

        # Rule 5: ClaimTicket for lowest-numbered ticket from DispatchCore().compute_frontier(...)
        # in the first repo in order that is unpaused and has starts_24h[repo] < config.daily_cap,
        # where the ticket is unclaimed and lint_ticket(t) == [].
        # Only applies while fewer than concurrency box claims are unfinished.
        unfinished_box_claims = 0
        for repo in world.repos:
            tickets_by_number = {t.number: t for t in repo.tickets}
            for num, claimant in repo.claims.items():
                if claimant == "box":
                    t = tickets_by_number.get(num)
                    if t is None or not t.is_done():
                        unfinished_box_claims += 1

        if not world.developer_paused and unfinished_box_claims < world.concurrency:
            for repo in world.repos:
                if repo.paused or not repo.accepting_new:
                    continue
                if world.starts_24h.get(repo.repo, 0) >= repo.config.daily_cap:
                    continue

                snapshot = WorldSnapshot(tickets=repo.tickets)
                frontier = self._dispatch_core.compute_frontier(snapshot)
                for ticket in frontier:
                    if ticket.number in repo.claims:
                        continue
                    if lint_ticket(ticket):
                        continue
                    return ClaimTicket(repo=repo.repo, ticket=ticket)

        # Rule 6: Otherwise Wait(now + poll_interval_minutes)
        return Wait(until=world.now + datetime.timedelta(minutes=world.poll_interval_minutes))

    def next_step(self_or_cls, world: BoxWorld | None = None) -> BoxStep:
        """Determine the next step the box should take given the world snapshot."""
        if isinstance(self_or_cls, BoxCore):
            core = self_or_cls
            target_world = world
        elif isinstance(self_or_cls, type) and issubclass(self_or_cls, BoxCore):
            core = self_or_cls()
            target_world = world
        else:
            core = BoxCore()
            target_world = self_or_cls  # type: ignore[assignment]

        if target_world is None:
            raise ValueError("world parameter is required")
        return core._next_step(target_world)

    @staticmethod
    def after_quota_error(
        world: BoxWorld,
        reset_at: datetime.datetime | None = None,
    ) -> tuple[datetime.datetime, datetime.datetime, list[BoxStep]]:
        """Compute new first failure, retry timestamp, and alert steps upon quota error."""
        first_failure = (
            world.quota_first_failure if world.quota_first_failure is not None else world.now
        )
        if (world.now - first_failure) > datetime.timedelta(hours=world.weekly_cap_after_hours):
            retry_at = world.now + datetime.timedelta(hours=world.weekly_cap_backoff_hours)
            alerts: list[BoxStep] = (
                [RaiseAlert(kind=AlertKind.weekly_cap)]
                if not world.weekly_cap_alert_open
                else []
            )
        else:
            if reset_at is not None:
                retry_at = reset_at + datetime.timedelta(seconds=60)
            else:
                retry_at = world.now + datetime.timedelta(hours=1)
            alerts = []
        return (first_failure, retry_at, alerts)

    @staticmethod
    def after_success(
        world: BoxWorld,
    ) -> tuple[datetime.datetime | None, datetime.datetime | None, list[BoxStep]]:
        """Reset quota failure and retry times on success, closing weekly cap alert if open."""
        if world.weekly_cap_alert_open:
            return (None, None, [CloseAlert(kind=AlertKind.weekly_cap)])
        return (None, None, [])
