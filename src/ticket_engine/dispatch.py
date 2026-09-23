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

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

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


@dataclass(frozen=True)
class DispatchResult:
    frontier: list[Ticket]
    actions: list[object]
    skipped_windows_tickets: list[Ticket]
    findings: list[ParseFinding]


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

        in_flight_count = len(snapshot.claims) + len(snapshot.open_prs)
        available_slots = max(0, snapshot.concurrency_limit - in_flight_count)

        for ticket in frontier:
            if ticket.runner == "windows":
                skipped_windows.append(ticket)
            else:
                if len(actions) < available_slots:
                    actions.append(StartTicketAction(ticket=ticket, runner=ticket.runner))

        return DispatchResult(
            frontier=frontier,
            actions=actions,
            skipped_windows_tickets=skipped_windows,
            findings=all_findings,
        )
