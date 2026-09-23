"""Morning report renderer — pure function of a world snapshot collection.

WHY THIS EXISTS
---------------
Ticket 08 and Phase 1 Spec §Reporting (User Stories 49-50) require a daily
summary issue in the engine repo listing, per target repo: merged PRs,
escalated PRs (with brief links), held PRs (with links), windows-waiting
frontier tickets, paused status, parse findings, and Jules quota standing.

ADR 0002 (everything public; privacy by checks) requires that the rendered
text never includes a real person's name, a secret, or a denylist entry.

The renderer is a pure function — MorningReportData in, Markdown string out —
so every possible output shape is testable with a static snapshot, with no
GitHub API calls in tests.
"""
from __future__ import annotations

import datetime
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from ticket_engine.dispatch import DispatchCore, MergedPR, OpenPR, WorldSnapshot

logger = logging.getLogger(__name__)

_LABEL_ESCALATED = "engine:escalated"
_LABEL_HOLD = "engine:hold"


@dataclass(frozen=True)
class MorningReportData:
    """All data the morning report renderer needs.

    ``repo_snapshots`` holds one WorldSnapshot per target repo, each with
    ``repo_name`` set to ``owner/repo``.  The dispatch core is called
    internally on each snapshot to derive windows-waiting and parse findings;
    the caller does not need to pre-compute those.

    ``jules_sessions_24h`` and ``jules_limit`` are global (engine-wide) values,
    not per-repo; the caller reads them from the Jules API before building this object.
    """

    repo_snapshots: Sequence[WorldSnapshot] = field(default_factory=list)
    jules_sessions_24h: int = 0
    jules_limit: int = 100
    now: datetime.datetime | None = None


def _pr_url(repo_name: str, pr_number: int) -> str:
    return f"https://github.com/{repo_name}/pull/{pr_number}"


def _render_repo_section(
    snapshot: WorldSnapshot,
    core: DispatchCore,
) -> str:
    repo = snapshot.repo_name or "(unknown repo)"
    lines: list[str] = [f"### {repo}", ""]

    ticket_map = {t.number: t for t in snapshot.tickets}

    # --- Merged PRs ---
    merged = [p for p in snapshot.merged_prs if isinstance(p, MergedPR)]
    lines.append(f"**Merged overnight:** {len(merged)}")
    for pr in merged:
        url = _pr_url(repo, pr.number) if repo else ""
        link = f"[#{pr.number}]({url})" if url else f"#{pr.number}"
        lines.append(f"- {link}: {pr.title}")
    lines.append("")

    # --- Escalated PRs ---
    escalated = [
        p for p in snapshot.open_prs
        if isinstance(p, OpenPR) and _LABEL_ESCALATED in p.labels
    ]
    lines.append(f"**Escalated (need review):** {len(escalated)}")
    for pr in escalated:
        url = _pr_url(repo, pr.number) if repo else ""
        link = f"[#{pr.number}]({url})" if url else f"#{pr.number}"
        ticket = ticket_map.get(pr.ticket_number)
        title = ticket.title if ticket else f"Ticket {pr.ticket_number}"
        lines.append(f"- {link}: {title}")
    lines.append("")

    # --- Held PRs ---
    held = [
        p for p in snapshot.open_prs
        if isinstance(p, OpenPR) and _LABEL_HOLD in p.labels
    ]
    lines.append(f"**Held (awaiting approval):** {len(held)}")
    for pr in held:
        url = _pr_url(repo, pr.number) if repo else ""
        link = f"[#{pr.number}]({url})" if url else f"#{pr.number}"
        ticket = ticket_map.get(pr.ticket_number)
        title = ticket.title if ticket else f"Ticket {pr.ticket_number}"
        lines.append(f"- {link}: {title}")
    lines.append("")

    # --- Windows-waiting (frontier tickets with runner=windows) ---
    result = core.evaluate(snapshot)
    windows_waiting = result.skipped_windows_tickets
    lines.append(f"**Windows-waiting:** {len(windows_waiting)}")
    for t in windows_waiting:
        lines.append(f"- {t.number:02d}: {t.title}")
    lines.append("")

    # --- Paused ---
    paused_label = "Yes ⚠️" if snapshot.paused else "No"
    lines.append(f"**Paused:** {paused_label}")
    lines.append("")

    # --- Parse findings ---
    findings = result.findings
    lines.append(f"**Parse findings:** {len(findings)}")
    for f_item in findings:
        file_part = f" ({f_item.file})" if f_item.file else ""
        lines.append(f"- {f_item.message}{file_part}")
    lines.append("")

    return "\n".join(lines)


def render_morning_report(data: MorningReportData) -> str:
    """Render the morning report as a Markdown string.

    Pure function: given MorningReportData, returns deterministic Markdown.
    No I/O is performed.

    ADR 0002: the output must never contain a real person's name, a token,
    or any denylist entry.  The renderer contains no hardcoded names; it only
    echoes ticket titles, PR numbers, and repo names supplied by the caller.
    """
    now = data.now or datetime.datetime.now(datetime.UTC)
    ts = now.strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"## Morning Report — {ts}",
        "",
        "_Auto-generated by ticket-engine. Do not edit by hand._",
        "",
        "---",
        "",
    ]

    core = DispatchCore()
    for snapshot in data.repo_snapshots:
        lines.append(_render_repo_section(snapshot, core))
        lines.append("---")
        lines.append("")

    # --- Jules quota (global) ---
    remaining = data.jules_limit - data.jules_sessions_24h
    lines.append(
        f"**Jules quota:** {data.jules_sessions_24h} / {data.jules_limit} used "
        f"({remaining} remaining)"
    )
    lines.append("")

    return "\n".join(lines)
