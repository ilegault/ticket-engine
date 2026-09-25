"""End-of-run dispatch report for the GitHub Actions job summary.

WHY THIS EXISTS
---------------
A dispatch run that starts nothing used to print only "Dispatched 0 ticket(s)",
which looks the same whether the repo is paused, the daily cap is spent, or every
agent ticket is waiting behind a `ready-for-developer` ticket that only the
developer can finish. This module turns what the run already knows into a short
markdown report that says which of those it was.

`build_run_report` is pure (no I/O, no clock, no network) so every line of the
report is testable from plain `Ticket` objects and a `RunFacts` record.
`write_step_summary` is the only function that touches the filesystem: it appends
the report to the file GitHub names in `$GITHUB_STEP_SUMMARY`, which Actions renders
on the run's summary page.

Privacy (ADR 0002): the report carries ticket numbers, titles, statuses, file paths
and counts. Never prompts, secrets or API responses beyond an HTTP status line.
"""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ticket_engine.parser import Ticket

# The permission a fine-grained PAT needs to read and set TICKET_ENGINE_PAUSED.
_VARIABLES_PERMISSION_HINT = (
    "Give `PIPELINE_TOKEN` the repository permission **Variables: Read and write**. "
    "Until then the circuit breaker cannot pause this repo either."
)


@dataclass
class RunFacts:
    """What one live dispatch run observed, filled in by `LiveDispatcher.dispatch`."""

    repo: str = ""
    paused: bool = False
    # Text of the error when reading TICKET_ENGINE_PAUSED failed; "" when the read worked.
    pause_check_error: str = ""
    # Set when dispatch returned before evaluating tickets (API failure, pause).
    stopped_early: str = ""
    jules_sessions_24h: int | None = None
    jules_limit: int = 100
    jules_reserve: int = 10
    repo_starts_24h: int | None = None
    daily_cap: int = 10
    concurrency: int = 2
    # Claim branches still held after this run's release of finished tickets.
    claims_in_flight: list[str] = field(default_factory=list)
    started: list[Ticket] = field(default_factory=list)
    claim_collisions: list[int] = field(default_factory=list)
    start_failures: list[int] = field(default_factory=list)


def _label(ticket: Ticket) -> str:
    return f"{ticket.number:02d}"


def _where(ticket: Ticket) -> str:
    if ticket.path is None:
        return ticket.effort or ""
    parts = ticket.path.parts
    if ".scratch" in parts:
        return "/".join(parts[parts.index(".scratch"):])
    return ticket.path.name


def find_duplicate_numbers(tickets: Sequence[Ticket]) -> dict[int, list[Ticket]]:
    """Ticket numbers used by more than one file. The engine keys tickets by number
    alone (blockers, claim branches), so a duplicate makes one of them invisible."""
    by_number: dict[int, list[Ticket]] = {}
    for t in tickets:
        by_number.setdefault(t.number, []).append(t)
    return {n: ts for n, ts in sorted(by_number.items()) if len(ts) > 1}


def _dependents(tickets: Sequence[Ticket]) -> dict[int, set[int]]:
    """Direct dependents: blocker number -> numbers of unfinished tickets it blocks."""
    out: dict[int, set[int]] = {}
    for t in tickets:
        if t.is_done():
            continue
        for b in t.blocked_by:
            out.setdefault(b, set()).add(t.number)
    return out


def held_up_by(number: int, tickets: Sequence[Ticket]) -> list[int]:
    """Every unfinished ticket that cannot start until `number` is done, directly or
    through a chain of blockers. Sorted, excluding `number` itself."""
    direct = _dependents(tickets)
    seen: set[int] = set()
    stack = [number]
    while stack:
        for dep in direct.get(stack.pop(), set()):
            if dep not in seen and dep != number:
                seen.add(dep)
                stack.append(dep)
    return sorted(seen)


def unfinished_blockers(ticket: Ticket, tickets: Sequence[Ticket]) -> list[str]:
    """Human-readable reasons this ticket's blockers are not satisfied, e.g.
    `36 (ready-for-developer)` or `99 (no such ticket)`. Mirrors the frontier rule
    in `DispatchCore.compute_frontier`: a blocker counts only when it `is_done()`."""
    by_number: dict[int, Ticket] = {t.number: t for t in tickets}
    reasons: list[str] = []
    for b in ticket.blocked_by:
        blocker = by_number.get(b)
        if blocker is None:
            reasons.append(f"{b:02d} (no such ticket)")
        elif not blocker.is_done():
            reasons.append(f"{b:02d} ({blocker.status})")
    return reasons


def needs_you_table(tickets: Sequence[Ticket]) -> list[str]:
    """Render the markdown table lines for ready-for-developer tickets.

    Shows what each developer ticket is waiting on and what it is holding up.
    Returns an empty list when there are no developer tickets. Pure.
    """
    dev = sorted((t for t in tickets if t.status == "ready-for-developer"), key=lambda t: t.number)
    if not dev:
        return []
    lines = [
        "| Ticket | Title | Waiting on | Holding up |",
        "|---|---|---|---|",
    ]
    for t in dev:
        waiting = unfinished_blockers(t, tickets)
        waiting_txt = ", ".join(waiting) if waiting else "ready now"
        held = held_up_by(t.number, tickets)
        held_txt = ", ".join(f"{n:02d}" for n in held) if held else "—"
        lines.append(f"| {_label(t)} | {t.title} | {waiting_txt} | {held_txt} |")
    return lines


def _limits_line(facts: RunFacts) -> str:
    bits: list[str] = []
    if facts.jules_sessions_24h is not None:
        bits.append(
            f"Jules sessions (24h, all repos): {facts.jules_sessions_24h}/{facts.jules_limit}, "
            f"reserve {facts.jules_reserve}"
        )
    if facts.repo_starts_24h is not None:
        bits.append(f"starts in this repo (24h): {facts.repo_starts_24h}/{facts.daily_cap}")
    bits.append(f"claims in flight: {len(facts.claims_in_flight)}/{facts.concurrency}")
    return " · ".join(bits)


def _limit_blocks(facts: RunFacts) -> list[str]:
    """Which configured limit, if any, stopped new starts this run."""
    out: list[str] = []
    if facts.jules_sessions_24h is not None:
        remaining = facts.jules_limit - facts.jules_sessions_24h
        if remaining < facts.jules_reserve:
            out.append(
                f"Jules quota reserve reached: {remaining} session(s) left today, "
                f"reserve is {facts.jules_reserve}."
            )
    if facts.repo_starts_24h is not None and facts.repo_starts_24h >= facts.daily_cap:
        out.append(f"Daily cap reached: {facts.repo_starts_24h}/{facts.daily_cap} starts in 24h.")
    if len(facts.claims_in_flight) >= facts.concurrency:
        out.append(
            f"Concurrency full: {len(facts.claims_in_flight)}/{facts.concurrency} tickets "
            "already claimed."
        )
    return out


def build_run_report(tickets: Sequence[Ticket], facts: RunFacts) -> str:
    """Markdown report of one dispatch run. Pure."""
    lines: list[str] = [f"## Ticket dispatch — {facts.repo or 'this repo'}", ""]

    # --- What happened -----------------------------------------------------
    if facts.started:
        lines.append(f"**Started {len(facts.started)} ticket(s):**")
        lines.extend(f"- {_label(t)} {t.title}" for t in facts.started)
    else:
        lines.append("**Started 0 tickets.**")
    lines.append("")

    # --- Why (run-level) ---------------------------------------------------
    notes: list[str] = []
    if facts.paused:
        notes.append("⏸️ Repo is **paused** (`TICKET_ENGINE_PAUSED`). Clear it to resume.")
    if facts.pause_check_error:
        msg = f"⚠️ Could not read `TICKET_ENGINE_PAUSED` ({facts.pause_check_error}); treated as not paused."
        if "403" in facts.pause_check_error:
            msg += " " + _VARIABLES_PERMISSION_HINT
        notes.append(msg)
    if facts.stopped_early:
        notes.append(f"⛔ Stopped before evaluating tickets: {facts.stopped_early}")
    if not facts.stopped_early and not facts.paused:
        notes.extend(f"🚦 {b}" for b in _limit_blocks(facts))
    for n in facts.claim_collisions:
        notes.append(f"Ticket {n:02d} was already claimed by another run; skipped.")
    for n in facts.start_failures:
        notes.append(f"Ticket {n:02d}: claim made but the Jules session failed to start.")
    if notes:
        lines.extend(f"- {n}" for n in notes)
        lines.append("")
    lines.append(_limits_line(facts))
    lines.append("")

    # --- Duplicate numbers -------------------------------------------------
    dups = find_duplicate_numbers(tickets)
    if dups:
        lines.append("### ❗ Duplicate ticket numbers")
        lines.append(
            "Blockers and claim branches use the number alone, so only one file per "
            "number is seen. Renumber all but one."
        )
        for n, ts in dups.items():
            lines.append(f"- **{n:02d}**: " + ", ".join(f"`{_where(t)}`" for t in ts))
        lines.append("")

    # --- Needs you ---------------------------------------------------------
    dev_table = needs_you_table(tickets)
    if dev_table:
        lines.append("### 🧑‍🔧 Needs you (`ready-for-developer`)")
        lines.extend(dev_table)
        lines.append("")

    # --- Waiting agent tickets --------------------------------------------
    started_numbers = {t.number for t in facts.started}
    waiting: list[tuple[Ticket, list[str]]] = []
    for t in sorted(tickets, key=lambda t: t.number):
        if t.status != "ready-for-agent" or t.number in started_numbers:
            continue
        reasons = unfinished_blockers(t, tickets)
        if reasons:
            waiting.append((t, reasons))
    if waiting:
        lines.append("### ⏳ Agent tickets waiting on blockers")
        lines.append("| Ticket | Title | Waiting on |")
        lines.append("|---|---|---|")
        for t, reasons in waiting:
            lines.append(f"| {_label(t)} | {t.title} | {', '.join(reasons)} |")
        lines.append("")

    # --- Other non-done states --------------------------------------------
    other = sorted(
        (t for t in tickets if t.status in ("in-progress", "blocked")),
        key=lambda t: t.number,
    )
    if other:
        lines.append("### 🔄 In progress or escalated")
        lines.extend(f"- {_label(t)} {t.title} — `{t.status}`" for t in other)
        lines.append("")

    unreadable = [t for t in tickets if any("status" in f.message.lower() for f in t.findings)]
    if unreadable:
        lines.append("### ❓ Tickets with an unreadable Status line (never counted as done)")
        for t in sorted(unreadable, key=lambda t: t.number):
            why = "; ".join(f.message for f in t.findings if "status" in f.message.lower())
            lines.append(f"- {_label(t)} `{_where(t)}`: {why}")
        lines.append("")

    done = sum(1 for t in tickets if t.is_done())
    lines.append(f"_{done} of {len(tickets)} tickets done._")
    return "\n".join(lines) + "\n"


def write_step_summary(markdown: str, environ: Mapping[str, str] | None = None) -> bool:
    """Append the report to `$GITHUB_STEP_SUMMARY`. Returns False when not running
    in GitHub Actions (variable unset) or the file cannot be written."""
    env = os.environ if environ is None else environ
    target = env.get("GITHUB_STEP_SUMMARY", "")
    if not target:
        return False
    try:
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(markdown)
    except OSError:
        return False
    return True
