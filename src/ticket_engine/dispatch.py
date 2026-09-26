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

LIVE SESSIONS
-------------
A session counts as live in any non-terminal Jules state (`LIVE_SESSION_STATES`).
An earlier version only recognised made-up names (RUNNING, ACTIVE, PENDING), so a
session that was PLANNING or waiting on a question looked dead and its claim could be
released under it. Those legacy names stay in the set so older snapshots still read
the same.

TICKET LINT (ADR 0005)
----------------------
A frontier ticket for which `ticket_lint.lint_ticket` finds problems is not started:
it would fail the integrity gate however well it is implemented. It is not removed
from the frontier, whose definition of blocked stays the single one in
`compute_frontier`; `DispatchResult.lint_held` names it for the reports.

WAITING SESSIONS (ADR 0004)
---------------------------
A Jules session can stop mid-ticket to ask a question (`AWAITING_USER_FEEDBACK`)
even with plan approval off and a prompt that says nobody is watching. Slackbot
ticket 46 did exactly that. Such a session is live, so its claim is never released
and the ticket waits for an answer nobody will give. `evaluate_waiting_sessions`
answers for the developer: an `AnswerSessionAction` telling the session to proceed
unattended, up to `config.max_auto_replies` times, then an
`EscalateWaitingSessionAction` that marks the ticket `blocked` on its claim branch and
tells the session to stop.

The dispatcher keeps no state, so the reply count is read back from the session's
own activity list: every message the engine sends starts with `AUTO_REPLY_MARKER` or
`STOP_MARKER`, and the core counts those. A session whose activities could not be
fetched carries no `activities` key and is never answered, because its reply count
is unknown. A session whose newest activity is the engine's own reply has not
processed it yet and is left alone, so two runs a minute apart do not spend two
replies on one question. The agent's question is never copied into the brief: it is
an API response body and the brief lands in a public repo (ADR 0002).
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
from ticket_engine.ticket_lint import lint_ticket

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
class MergedPR:
    """A recently merged pull request, used by the morning report renderer."""

    number: int
    title: str
    ticket_number: int = 0
    merged_at: datetime.datetime | None = None


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
class AnswerSessionAction:
    """Reply to a Jules session waiting on a question: proceed unattended (ADR 0004)."""

    session_name: str
    ticket_number: int
    message: str
    reply_number: int


@dataclass(frozen=True)
class EscalateWaitingSessionAction:
    """A session kept asking after every allowed reply. Mark the ticket blocked on its
    claim branch, then send `stop_message` so the session stops (ADR 0004)."""

    ticket: Ticket
    session_name: str
    claim_ref: str
    ticket_path: str
    brief: str
    stop_message: str
    replies: int


@dataclass(frozen=True)
class EscalatedSessionStillWaiting:
    """Report-only: a session the engine already escalated is still waiting."""

    ticket_number: int
    claim_ref: str


# Every message the engine sends a session starts with one of these, so the core can
# count its own replies from the session's activity list without keeping state.
AUTO_REPLY_MARKER = "[ticket-engine auto-reply]"
STOP_MARKER = "[ticket-engine escalated]"

AUTO_REPLY_TEXT = (
    f"{AUTO_REPLY_MARKER} No human is watching this session and nobody will answer "
    "questions in it. Do not ask for confirmation, approval, or feedback. Make the "
    "decision yourself from the ticket, its ADRs, and AGENTS.md, and carry on to the "
    "end: run the full gate, mark the ticket done, and finish so the pull request "
    "opens. If you are truly blocked (the ticket is ambiguous in a way that changes "
    "the result, or it needs bench work or a human judgement call), set the ticket's "
    "`Status:` to `blocked`, write the escalation brief under `## Comments`, and "
    "finish the session instead of waiting."
)

_WAITING_STATE = "AWAITING_USER_FEEDBACK"
_SESSION_TITLE_RE = re.compile(r"^(?P<effort>.+)-(?P<num>\d+):")


# Every non-terminal Jules session state, plus legacy names older snapshots used.
LIVE_SESSION_STATES = frozenset(
    {
        "QUEUED",
        "PLANNING",
        "AWAITING_PLAN_APPROVAL",
        "AWAITING_USER_FEEDBACK",
        "IN_PROGRESS",
        "PAUSED",
        "RUNNING",
        "ACTIVE",
        "PENDING",
    }
)


def is_live_session_state(state: object) -> bool:
    """True when a Jules session in this state is still working or waiting to work."""
    return str(state or "").strip().upper() in LIVE_SESSION_STATES


def _session_source_matches(sess: dict[str, Any], repo: str) -> bool:
    target = repo.strip().strip("/")
    source = str((sess.get("sourceContext") or {}).get("source", "")).strip().strip("/")
    return source in {target, f"sources/github/{target}"}


def session_resource_name(sess: dict[str, Any]) -> str:
    """The `sessions/<id>` name the Jules API addresses a session by. Pure."""
    for key in ("name", "id"):
        val = str(sess.get(key) or "").strip()
        if val:
            return val if val.startswith("sessions/") else f"sessions/{val}"
    return ""


def ticket_repo_path(ticket: Ticket) -> str:
    """The ticket file's path relative to the repo root, forward slashes. Pure."""
    if ticket.path is not None:
        parts = ticket.path.parts
        if ".scratch" in parts:
            return "/".join(parts[parts.index(".scratch"):])
    effort = ticket.effort or "phase-1"
    return f".scratch/{effort}/issues/{ticket.number:02d}-{ticket.slug}.md"


def _engine_message(activity: dict[str, Any]) -> str:
    """The text of an activity if it is a user message, else ""."""
    msg = activity.get("userMessaged")
    if isinstance(msg, dict):
        return str(msg.get("userMessage") or "")
    return ""


def _ordered_activities(activities: Sequence[Any]) -> list[dict[str, Any]]:
    acts = [a for a in activities if isinstance(a, dict)]
    if acts and all(a.get("createTime") for a in acts):
        acts.sort(key=lambda a: str(a["createTime"]))
    return acts


def count_repo_starts(
    sessions: Sequence[dict[str, Any] | object],
    repo: str,
    now: datetime.datetime,
    hours: int = 24,
) -> int:
    """Count Jules sessions for exactly this repo created in the last `hours`. Pure.

    Feeds the per-repo daily cap. The old count took every session in the Jules list
    (all repos, any age), so ten sessions anywhere stopped the repo for good.
    """
    target = repo.strip().strip("/")
    wanted = {target, f"sources/github/{target}"}
    cutoff = now - datetime.timedelta(hours=hours)
    count = 0
    for sess in sessions:
        if not isinstance(sess, dict):
            continue
        source = str((sess.get("sourceContext") or {}).get("source", "")).strip().strip("/")
        if source not in wanted:
            continue
        raw = str(sess.get("createTime") or "")
        try:
            created = datetime.datetime.fromisoformat(raw)
        except ValueError:
            continue
        if created.tzinfo is None:
            created = created.replace(tzinfo=datetime.UTC)
        if created >= cutoff:
            count += 1
    return count


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
    # Morning report fields: set by the live reporter, ignored by the dispatch core.
    repo_name: str = ""
    merged_prs: Sequence[MergedPR | object] = field(default_factory=list)


@dataclass(frozen=True)
class DispatchResult:
    frontier: list[Ticket]
    actions: list[object]
    skipped_windows_tickets: list[Ticket]
    findings: list[ParseFinding]
    # Frontier tickets not started because lint found they cannot land (ADR 0005).
    lint_held: list[Ticket] = field(default_factory=list)


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


def assemble_waiting_session_brief(
    ticket: Ticket,
    claim_ref: str,
    session_name: str,
    replies: int,
    date: str,
) -> str:
    """Escalation brief for a session that kept asking after every allowed reply.

    Same layout as `assemble_escalation_brief` (the ticket skill's format), one
    attempt line per reply the engine sent. The agent's question is deliberately not
    included: it is an API response body and this text is committed to a public repo.
    """
    goal = ""
    wtb_match = re.search(
        r"^(?:\*\*)?What to build:(?:\*\*)?\s*(.+)$",
        ticket.raw_text or "",
        re.MULTILINE | re.IGNORECASE,
    )
    if wtb_match:
        sentences = re.split(r"(?<=[.!?])\s+", wtb_match.group(1).strip())
        goal = sentences[0].strip()
    goal = goal or ticket.title

    attempts = [
        f"Attempt {i}: engine auto-reply told the session to proceed unattended "
        "→ the session stopped to ask again"
        for i in range(1, replies + 1)
    ]
    parts = [
        f"## Escalation — {date}",
        f"Ticket: {ticket.number:02d} {ticket.title}   Branch: {claim_ref}",
        f"Goal: {goal}",
        *attempts,
        "Failing output (exact, trimmed to the relevant lines):",
        "```",
        f"Jules session {session_name} is in {_WAITING_STATE} after {replies} auto-replies. "
        "Its question is in the Jules web UI; it is not copied here (ADR 0002).",
        "```",
        "Decision needed: Answer the session's question in Jules, or rewrite the ticket "
        "so it can be finished without one, then delete the claim branch to retry.",
    ]
    return "\n".join(parts) + "\n"


def stop_message_text(replies: int) -> str:
    return (
        f"{STOP_MARKER} This session asked for input again after {replies} auto-replies. "
        "The engine has marked the ticket blocked and will not answer again. Stop work "
        "now and do not push further changes."
    )


def evaluate_waiting_sessions(snapshot: WorldSnapshot) -> list[object]:
    """Decide what to do about this repo's Jules sessions that are waiting on a
    question (ADR 0004). Pure: reads only the snapshot."""
    cfg = snapshot.config
    now = snapshot.now or datetime.datetime.now(datetime.UTC)
    tickets = {t.number: t for t in snapshot.tickets}
    actions: list[object] = []

    for sess in snapshot.jules_sessions:
        if not isinstance(sess, dict):
            continue
        if str(sess.get("state") or "").strip().upper() != _WAITING_STATE:
            continue
        if snapshot.repo_name and not _session_source_matches(sess, snapshot.repo_name):
            continue
        m = _SESSION_TITLE_RE.match(str(sess.get("title") or ""))
        if not m:
            continue
        ticket = tickets.get(int(m.group("num")))
        if ticket is None or ticket.is_done():
            continue
        if ticket.effort and m.group("effort") != ticket.effort:
            continue
        activities = sess.get("activities")
        if not isinstance(activities, (list, tuple)):
            continue  # history unknown: never answer blind
        name = session_resource_name(sess)
        if not name:
            continue

        acts = _ordered_activities(activities)
        messages = [_engine_message(a) for a in acts]
        effort = ticket.effort or "phase-1"
        claim_ref = f"claim/{effort}/{ticket.number:02d}"

        if any(msg.startswith(STOP_MARKER) for msg in messages):
            actions.append(EscalatedSessionStillWaiting(ticket.number, claim_ref))
            continue
        if acts and messages[-1].startswith(AUTO_REPLY_MARKER):
            continue  # the session has not processed the last reply yet
        replies = sum(1 for msg in messages if msg.startswith(AUTO_REPLY_MARKER))

        if replies < cfg.max_auto_replies:
            actions.append(
                AnswerSessionAction(
                    session_name=name,
                    ticket_number=ticket.number,
                    message=AUTO_REPLY_TEXT,
                    reply_number=replies + 1,
                )
            )
        else:
            actions.append(
                EscalateWaitingSessionAction(
                    ticket=ticket,
                    session_name=name,
                    claim_ref=claim_ref,
                    ticket_path=ticket_repo_path(ticket),
                    brief=assemble_waiting_session_brief(
                        ticket, claim_ref, name, replies, now.strftime("%Y-%m-%d")
                    ),
                    stop_message=stop_message_text(replies),
                    replies=replies,
                )
            )
    return actions


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
                        title = sess.get("title", "")
                        if (
                            is_live_session_state(sess.get("state"))
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

        # 1b. Sessions waiting on a question (ADR 0004). Before the pause check:
        # a paused repo starts nothing new, but work in flight still finishes.
        actions.extend(evaluate_waiting_sessions(snapshot))

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

        lint_held: list[Ticket] = []
        for ticket in frontier:
            if ticket.runner == "windows":
                skipped_windows.append(ticket)
            elif is_ticket_claimed(ticket, active_claims):
                continue
            elif lint_ticket(ticket):
                # ADR 0005: a ticket that cannot land as written is not started. It
                # stays on the frontier (one definition of blocked) and is reported.
                lint_held.append(ticket)
            else:
                if len([a for a in actions if isinstance(a, StartTicketAction)]) < available_slots:
                    actions.append(StartTicketAction(ticket=ticket, runner=ticket.runner))

        return DispatchResult(
            frontier=frontier,
            actions=actions,
            skipped_windows_tickets=skipped_windows,
            findings=all_findings,
            lint_held=lint_held,
        )
