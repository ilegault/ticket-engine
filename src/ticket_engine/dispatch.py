"""Pure dispatch core.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Dispatch core) and ADR 0001/0003 require
that the dispatch core is a pure function: given a world snapshot, it returns
actions and evaluations with zero I/O (no network, no subprocess, no file writes,
no clock reads).
This ensures every scheduling decision, dependency hold, concurrency gate, and
runner partition is testable reproducibly with static snapshots.
"""
from __future__ import annotations

import datetime
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from ticket_engine.config import RepoConfig
from ticket_engine.parser import ParseFinding, Ticket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StartTicketAction:
    ticket: Ticket
    runner: str = "any"


@dataclass(frozen=True)
class WorldSnapshot:
    tickets: Sequence[Ticket] = field(default_factory=list)
    claims: set[str] | Sequence[str] = field(default_factory=set)
    open_prs: Sequence[object] = field(default_factory=list)
    concurrency_limit: int = 2
    config: RepoConfig = field(default_factory=RepoConfig)
    jules_sessions_count_24h: int = 0
    repo_starts_last_24h: int = 0
    paused: bool = False
    now: datetime.datetime | None = None


@dataclass(frozen=True)
class DispatchResult:
    frontier: list[Ticket]
    actions: list[object]
    skipped_windows_tickets: list[Ticket]
    findings: list[ParseFinding]


def is_ticket_claimed(ticket: Ticket, claims: set[str] | Sequence[str]) -> bool:
    """Check if a ticket is already claimed by any claim branch or identifier."""
    num_str_2 = f"{ticket.number:02d}"
    num_str = str(ticket.number)
    for c in claims:
        c_str = str(c).strip()
        parts = c_str.split("/")
        if parts[-1] in (num_str_2, num_str):
            return True
        if c_str in (num_str_2, num_str):
            return True
    return False


class DispatchCore:
    """Pure dispatch evaluator for ticket-engine."""

    def compute_frontier(self, snapshot: WorldSnapshot) -> list[Ticket]:
        # Build map of ticket number to ticket
        ticket_map = {t.number: t for t in snapshot.tickets}

        frontier: list[Ticket] = []
        for ticket in snapshot.tickets:
            # Only ready-for-agent tickets can appear on the frontier
            if ticket.status != "ready-for-agent":
                continue

            # Check all blockers
            all_blockers_done = True
            for blocker_num in ticket.blocked_by:
                blocker = ticket_map.get(blocker_num)
                if blocker is None or not blocker.is_done():
                    all_blockers_done = False
                    break

            if all_blockers_done:
                frontier.append(ticket)

        # Sort first by number
        frontier.sort(key=lambda t: t.number)
        return frontier

    def evaluate(self, snapshot: WorldSnapshot) -> DispatchResult:
        frontier = self.compute_frontier(snapshot)

        # Gather all parse findings across all tickets
        all_findings: list[ParseFinding] = []
        for ticket in snapshot.tickets:
            all_findings.extend(ticket.findings)

        skipped_windows: list[Ticket] = []
        actions: list[object] = []

        cfg = snapshot.config
        concurrency = cfg.concurrency if snapshot.config else snapshot.concurrency_limit

        # 1. Check if repo is paused
        if snapshot.paused:
            return DispatchResult(
                frontier=frontier,
                actions=[],
                skipped_windows_tickets=[t for t in frontier if t.runner == "windows"],
                findings=all_findings,
            )

        # 2. Check quota reserve: never start when fewer than reserve remain
        remaining_quota = cfg.jules_limit - snapshot.jules_sessions_count_24h
        if remaining_quota < cfg.jules_reserve:
            return DispatchResult(
                frontier=frontier,
                actions=[],
                skipped_windows_tickets=[t for t in frontier if t.runner == "windows"],
                findings=all_findings,
            )

        # 3. Check daily cap
        if snapshot.repo_starts_last_24h >= cfg.daily_cap:
            return DispatchResult(
                frontier=frontier,
                actions=[],
                skipped_windows_tickets=[t for t in frontier if t.runner == "windows"],
                findings=all_findings,
            )

        # 4. Check available slots
        in_flight_count = len(snapshot.claims) + len(snapshot.open_prs)
        available_slots = max(0, concurrency - in_flight_count)

        # Also bound by remaining daily cap and remaining quota above reserve
        remaining_cap = max(0, cfg.daily_cap - snapshot.repo_starts_last_24h)
        remaining_quota_starts = max(0, remaining_quota - cfg.jules_reserve + 1)
        available_slots = min(available_slots, remaining_cap, remaining_quota_starts)

        for ticket in frontier:
            if ticket.runner == "windows":
                skipped_windows.append(ticket)
            elif is_ticket_claimed(ticket, snapshot.claims):
                # Claim collision: ticket already claimed, skip it
                continue
            else:
                if len(actions) < available_slots:
                    actions.append(StartTicketAction(ticket=ticket, runner=ticket.runner))

        return DispatchResult(
            frontier=frontier,
            actions=actions,
            skipped_windows_tickets=skipped_windows,
            findings=all_findings,
        )
