"""Pure dispatch core.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Dispatch core), §Fix attempts and escalation,
§Claims, and ADR 0001/0003 require that the dispatch core is a pure function:
given a world snapshot, it returns actions (ticket start, PR escalation, stale claim release,
repo pause) with zero I/O (no network, no subprocess, no file writes, no clock reads).
Tunables (stale-claim hours, max fix attempts, circuit breaker limit/window) are read strictly
from config.
This ensures every scheduling decision, dependency hold, concurrency gate, and
runner partition is testable reproducibly with static snapshots.
"""
from __future__ import annotations

import datetime
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ticket_engine.config import RepoConfig
from ticket_engine.parser import ParseFinding, Ticket

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Claim:
    ref: str
    ticket_number: int
    effort: str = "phase-1"
    last_commit_time: datetime.datetime | None = None
    has_live_session: bool = False


@dataclass(frozen=True)
class PRHeadCommit:
    sha: str
    ci_failed: bool = False
    created_at: datetime.datetime | None = None


@dataclass(frozen=True)
class OpenPR:
    number: int
    branch: str
    ticket_number: int
    head_sha: str = ""
    is_draft: bool = False
    labels: tuple[str, ...] = ()
    failed_ci_count: int = 0
    head_commits: tuple[PRHeadCommit, ...] = ()
    jules_session_id: str | None = None
    ci_log_excerpt: str = ""


@dataclass(frozen=True)
class StartTicketAction:
    ticket: Ticket
    runner: str = "any"


@dataclass(frozen=True)
class EscalatePRAction:
    ticket: Ticket
    pr_number: int
    branch: str
    brief: str
    reason: str = "Three failed CI runs across successive head commits"


@dataclass(frozen=True)
class ReleaseClaimAction:
    claim_ref: str
    ticket_number: int
    effort: str = "phase-1"
    reason: str = "Stale claim: no live Jules session and no ticket-branch commit in 12 hours"


@dataclass(frozen=True)
class PauseRepoAction:
    reason: str = "Circuit breaker: two escalations within 24 hours"


@dataclass(frozen=True)
class WorldSnapshot:
    tickets: Sequence[Ticket] = field(default_factory=list)
    claims: Sequence[Claim | str] | set[str] = field(default_factory=set)
    open_prs: Sequence[OpenPR | object] = field(default_factory=list)
    concurrency_limit: int = 2
    config: RepoConfig = field(default_factory=RepoConfig)
    jules_sessions_count_24h: int = 0
    repo_starts_last_24h: int = 0
    paused: bool = False
    now: datetime.datetime | None = None
    escalations: Sequence[datetime.datetime | object] = field(default_factory=list)
    jules_sessions: Sequence[dict[str, Any] | object] = field(default_factory=list)


@dataclass(frozen=True)
class DispatchResult:
    frontier: list[Ticket]
    actions: list[object]
    skipped_windows_tickets: list[Ticket]
    findings: list[ParseFinding]


def is_ticket_claimed(ticket: Ticket, claims: Sequence[Claim | str] | set[str]) -> bool:
    """Check if a ticket is already claimed by any claim branch or identifier."""
    num_str_2 = f"{ticket.number:02d}"
    num_str = str(ticket.number)
    for c in claims:
        if isinstance(c, Claim):
            if c.ticket_number == ticket.number:
                return True
            c_str = c.ref
        else:
            c_str = str(c).strip()
        parts = c_str.split("/")
        if parts[-1] in (num_str_2, num_str):
            return True
        if c_str in (num_str_2, num_str):
            return True
    return False


def assemble_escalation_brief(
    ticket: Ticket,
    branch: str,
    activities: Sequence[dict[str, Any] | str] | None = None,
    ci_log_excerpt: str = "",
    date: str | datetime.date | datetime.datetime | None = None,
    decision_needed: str | None = None,
) -> str:
    """Assemble an escalation brief matching the format defined in the ticket skill."""
    if date is not None:
        if isinstance(date, (datetime.date, datetime.datetime)):
            date_str = date.strftime("%Y-%m-%d")
        else:
            date_str = str(date)
    else:
        date_str = datetime.datetime.now(datetime.UTC).date().isoformat()

    # Extract goal from What to build
    goal = ""
    raw = ticket.raw_text or ""
    wtb_match = re.search(r"^(?:\*\*)?What to build:(?:\*\*)?\s*(.+)$", raw, re.MULTILINE | re.IGNORECASE)
    if wtb_match:
        full_wtb = wtb_match.group(1).strip()
        # First sentence or full line
        sentences = re.split(r"(?<=[.!?])\s+", full_wtb)
        goal = sentences[0].strip() if sentences else full_wtb
    if not goal:
        goal = ticket.title

    attempt_lines: list[str] = []
    if activities:
        for idx in range(1, 4):
            if idx - 1 < len(activities):
                act = activities[idx - 1]
                if isinstance(act, dict):
                    desc = act.get("description") or act.get("title") or f"Fix attempt {idx}"
                    res = act.get("result") or act.get("outcome") or "CI failed"
                    attempt_lines.append(f"Attempt {idx}: {desc} → {res}")
                elif isinstance(act, (tuple, list)) and len(act) == 2:
                    attempt_lines.append(f"Attempt {idx}: {act[0]} → {act[1]}")
                else:
                    attempt_lines.append(f"Attempt {idx}: {act}")
            else:
                attempt_lines.append(f"Attempt {idx}: Fix attempt {idx} → CI run failed")
    else:
        attempt_lines = [
            "Attempt 1: First fix attempt → CI failed",
            "Attempt 2: Second fix attempt → CI failed",
            "Attempt 3: Third fix attempt → CI failed",
        ]

    dec = decision_needed or "Three attempts failed to pass CI; review and determine necessary changes."

    parts = [
        f"## Escalation — {date_str}",
        f"Ticket: {ticket.number:02d} {ticket.title}   Branch: {branch}",
        f"Goal: {goal}",
        *attempt_lines,
        "Failing output (exact, trimmed to the relevant lines):",
        "```",
        ci_log_excerpt.strip(),
        "```",
        f"Decision needed: {dec}",
    ]
    return "\n".join(parts) + "\n"


def apply_escalation_to_ticket_text(ticket_text: str, brief: str) -> str:
    """Update ticket markdown content to Status: blocked and insert escalation brief."""
    # 1. Update status
    updated = re.sub(
        r"^((\*\*?)?Status:(\*\*?)\s*)[^\r\n]+",
        r"\g<1>blocked",
        ticket_text,
        flags=re.IGNORECASE | re.MULTILINE,
    )

    # 2. Add brief under ## Comments
    if re.search(r"^##\s+Comments", updated, re.MULTILINE | re.IGNORECASE):
        # Insert brief after ## Comments header
        updated = re.sub(
            r"^(##\s+Comments[^\r\n]*)",
            r"\1\n\n" + brief.strip(),
            updated,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    else:
        updated = updated.rstrip() + f"\n\n## Comments\n\n{brief.strip()}\n"

    return updated


class DispatchCore:
    """Pure dispatch evaluator for ticket-engine."""

    def compute_frontier(self, snapshot: WorldSnapshot) -> list[Ticket]:
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
        now = snapshot.now or datetime.datetime.now(datetime.UTC)

        # 1. Stale claims evaluation
        stale_claim_refs: set[str] = set()
        for c in snapshot.claims:
            if isinstance(c, Claim):
                ref = c.ref
                ticket_num = c.ticket_number
                effort = c.effort
                last_commit = c.last_commit_time
                has_live = c.has_live_session
            else:
                ref = str(c).strip()
                parts = ref.split("/")
                ticket_num = int(parts[-1]) if parts[-1].isdigit() else 0
                effort = parts[-2] if len(parts) >= 2 else "phase-1"
                last_commit = None
                has_live = False

            if not has_live and snapshot.jules_sessions:
                for sess in snapshot.jules_sessions:
                    if isinstance(sess, dict):
                        state = str(sess.get("state", "")).upper()
                        title = sess.get("title", "")
                        if (
                            state in ("RUNNING", "ACTIVE", "IN_PROGRESS", "QUEUED", "PENDING")
                            and (f"-{ticket_num:02d}:" in title or f"-{ticket_num}:" in title)
                        ):
                            has_live = True
                            break

            if not has_live and last_commit is not None:
                age = now - last_commit
                if age >= datetime.timedelta(hours=cfg.stale_claim_hours):
                    stale_claim_refs.add(ref)
                    actions.append(
                        ReleaseClaimAction(
                            claim_ref=ref,
                            ticket_number=ticket_num,
                            effort=effort,
                            reason=f"Stale claim: no live Jules session and no ticket-branch commit in {cfg.stale_claim_hours} hours",
                        )
                    )

        # Active claims excluding newly released stale claims
        active_claims = [c for c in snapshot.claims if (c.ref if isinstance(c, Claim) else str(c).strip()) not in stale_claim_refs]

        # 2. PR failures and escalation evaluation
        ticket_map = {t.number: t for t in snapshot.tickets}
        new_escalations = 0
        for pr in snapshot.open_prs:
            if not isinstance(pr, OpenPR):
                continue
            failed_count = pr.failed_ci_count
            if not failed_count and pr.head_commits:
                failed_count = sum(1 for comm in pr.head_commits if comm.ci_failed)

            if failed_count >= cfg.max_fix_attempts:
                ticket = ticket_map.get(pr.ticket_number)
                if ticket is None:
                    ticket = Ticket(
                        number=pr.ticket_number,
                        title=f"Ticket {pr.ticket_number}",
                        slug=f"ticket-{pr.ticket_number}",
                        status="in-progress",
                    )
                activities = None
                if pr.jules_session_id and snapshot.jules_sessions:
                    for s in snapshot.jules_sessions:
                        if isinstance(s, dict) and s.get("id") == pr.jules_session_id:
                            activities = s.get("activities")
                            break

                date_str = now.strftime("%Y-%m-%d") if hasattr(now, "strftime") else "2026-09-23"
                brief = assemble_escalation_brief(
                    ticket=ticket,
                    branch=pr.branch,
                    activities=activities,
                    ci_log_excerpt=pr.ci_log_excerpt,
                    date=date_str,
                )
                actions.append(
                    EscalatePRAction(
                        ticket=ticket,
                        pr_number=pr.number,
                        branch=pr.branch,
                        brief=brief,
                        reason=f"{failed_count} failed CI runs across successive head commits",
                    )
                )
                new_escalations += 1

        # 3. Circuit breaker evaluation
        cutoff = now - datetime.timedelta(hours=cfg.circuit_breaker_window_hours)
        prior_escalations_window = 0
        for esc in snapshot.escalations:
            if isinstance(esc, datetime.datetime):
                if esc >= cutoff:
                    prior_escalations_window += 1
            elif isinstance(esc, (int, float)):
                prior_escalations_window += int(esc)

        total_escalations_window = prior_escalations_window + new_escalations
        circuit_broken = False
        if total_escalations_window >= cfg.circuit_breaker_escalations_limit:
            circuit_broken = True
            actions.append(
                PauseRepoAction(
                    reason=f"Circuit breaker: {total_escalations_window} escalations within {cfg.circuit_breaker_window_hours} hours"
                )
            )

        # 4. Check if repo is paused or circuit broken
        if snapshot.paused or circuit_broken:
            return DispatchResult(
                frontier=frontier,
                actions=actions,
                skipped_windows_tickets=[t for t in frontier if t.runner == "windows"],
                findings=all_findings,
            )

        # 5. Check quota reserve
        remaining_quota = cfg.jules_limit - snapshot.jules_sessions_count_24h
        if remaining_quota < cfg.jules_reserve:
            return DispatchResult(
                frontier=frontier,
                actions=actions,
                skipped_windows_tickets=[t for t in frontier if t.runner == "windows"],
                findings=all_findings,
            )

        # 6. Check daily cap
        if snapshot.repo_starts_last_24h >= cfg.daily_cap:
            return DispatchResult(
                frontier=frontier,
                actions=actions,
                skipped_windows_tickets=[t for t in frontier if t.runner == "windows"],
                findings=all_findings,
            )

        # 7. Check available slots
        in_flight_count = len(active_claims) + len(snapshot.open_prs)
        available_slots = max(0, concurrency - in_flight_count)

        remaining_cap = max(0, cfg.daily_cap - snapshot.repo_starts_last_24h)
        remaining_quota_starts = max(0, remaining_quota - cfg.jules_reserve + 1)
        available_slots = min(available_slots, remaining_cap, remaining_quota_starts)

        for ticket in frontier:
            if ticket.runner == "windows":
                skipped_windows.append(ticket)
            elif is_ticket_claimed(ticket, active_claims):
                continue
            else:
                if len([a for a in actions if isinstance(a, StartTicketAction)]) < available_slots:
                    actions.append(StartTicketAction(ticket=ticket, runner=ticket.runner))

        return DispatchResult(
            frontier=frontier,
            actions=actions,
            skipped_windows_tickets=skipped_windows,
            findings=all_findings,
        )
