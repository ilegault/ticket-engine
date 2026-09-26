"""Box status texts: pure rendering and parsing for all public box messages.

WHY THIS EXISTS
---------------
ADR 0007 rule 4 and ADR 0002.
The engine and target repos are public (ADR 0002). The box runs unsupervised
in the developer's environment with broad execution capabilities. To ensure
nothing private (raw errors, tracebacks, personal logs, secret tokens) leaks
into GitHub issues or comments, ADR 0007 rule 4 requires:

"The box posts only fixed-template text to the engine repo. The box status
issue and box alerts are short status lines: states, ticket IDs, times. Raw
error output and logs are never posted. A test enforces this."

This pure module is the single authority for formatting and parsing all public
texts concerning the box:
- The pinned, locked Box Status issue (rendered and parsed).
- Box alert issues for weekly quota cap, login expiration, and box silence.
- Escalation issues opened in target repos.

All inputs are strictly typed and validated against allowlists with no free-text
parameters. Later tickets (24, 26, 30, 31, 33) consume this module rather than
formatting texts themselves.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from enum import Enum

_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_IDENT_RE = re.compile(r"^[\w.-]+$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z$")

__all__ = [
    "AlertKind",
    "BoxState",
    "BoxStatus",
    "EscalationReason",
    "TicketRef",
    "parse_box_status",
    "parse_escalation_issue_title",
    "render_box_alert",
    "render_box_status",
    "render_escalation_issue",
]


class BoxState(str, Enum):
    """Lifecycle and operational state of the box worker."""

    working = "working"
    idle = "idle"
    paused_quota = "paused_quota"
    paused_weekly_cap = "paused_weekly_cap"
    login_expired = "login_expired"


class AlertKind(str, Enum):
    """Categories of operational alerts concerning the box."""

    weekly_cap = "weekly_cap"
    login_expired = "login_expired"
    box_silent = "box_silent"


class EscalationReason(str, Enum):
    """Categorical reasons for escalating a ticket issue to the developer."""

    ci_failed = "ci_failed"
    kept_asking = "kept_asking"
    resumes_exhausted = "resumes_exhausted"


@dataclass(frozen=True)
class TicketRef:
    """Reference to a ticket within a specific repository."""

    repo: str
    number: int

    def __post_init__(self) -> None:
        if not _REPO_RE.match(self.repo):
            raise ValueError(f"Invalid repository reference: {self.repo!r}")


@dataclass(frozen=True)
class BoxStatus:
    """Snapshot of the box worker's current operational heartbeat."""

    checked_in_at: datetime.datetime
    state: BoxState
    current: TicketRef | None = None
    paused_until: datetime.datetime | None = None

    def __post_init__(self) -> None:
        if self.checked_in_at.tzinfo is None:
            raise ValueError("checked_in_at must be timezone-aware (UTC)")
        utc_checked_in = (
            self.checked_in_at.astimezone(datetime.UTC).replace(second=0, microsecond=0)
        )
        object.__setattr__(self, "checked_in_at", utc_checked_in)

        if isinstance(self.state, str) and not isinstance(self.state, BoxState):
            object.__setattr__(self, "state", BoxState(self.state))

        if self.paused_until is not None:
            if self.paused_until.tzinfo is None:
                raise ValueError("paused_until must be timezone-aware (UTC)")
            utc_paused = (
                self.paused_until.astimezone(datetime.UTC).replace(second=0, microsecond=0)
            )
            object.__setattr__(self, "paused_until", utc_paused)


def _format_iso(dt: datetime.datetime) -> str:
    """Format a timezone-aware datetime as YYYY-MM-DDTHH:MMZ."""
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return dt.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%MZ")


def _parse_iso(text: str) -> datetime.datetime | None:
    """Parse a strict YYYY-MM-DDTHH:MMZ timestamp into UTC aware datetime."""
    if not isinstance(text, str) or not _ISO_RE.match(text):
        return None
    try:
        dt = datetime.datetime.fromisoformat(text)
        if dt.tzinfo is None:
            return None
        return dt.astimezone(datetime.UTC).replace(second=0, microsecond=0)
    except (ValueError, TypeError):
        return None


def render_box_status(s: BoxStatus) -> str:
    """Render the machine-readable body for the box status issue."""
    current_str = (
        f"{s.current.repo} #{s.current.number:02d}" if s.current is not None else "none"
    )
    paused_str = (
        _format_iso(s.paused_until) if s.paused_until is not None else "none"
    )
    lines = [
        "## Box status",
        f"Checked in: {_format_iso(s.checked_in_at)}",
        f"State: {s.state.value}",
        f"Current: {current_str}",
        f"Paused until: {paused_str}",
    ]
    return "\n".join(lines) + "\n"


def parse_box_status(text: str) -> BoxStatus | None:
    """Parse the body of a box status issue into a BoxStatus instance.

    Returns None if the body is malformed or missing required keys. Never raises.
    """
    if not text or not isinstance(text, str):
        return None

    try:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines or lines[0] != "## Box status":
            return None

        data: dict[str, str] = {}
        for line in lines[1:]:
            if ":" not in line:
                return None
            k, v = line.split(":", 1)
            data[k.strip()] = v.strip()

        if "Checked in" not in data or "State" not in data:
            return None

        checked_in_at = _parse_iso(data["Checked in"])
        if checked_in_at is None:
            return None

        try:
            state = BoxState(data["State"])
        except ValueError:
            return None

        current_str = data.get("Current")
        if current_str is None:
            return None

        current: TicketRef | None = None
        if current_str.lower() != "none":
            m = re.match(r"^([\w.-]+/[\w.-]+)\s*#(\d+)$", current_str)
            if not m:
                return None
            current = TicketRef(repo=m.group(1), number=int(m.group(2)))

        paused_str = data.get("Paused until")
        if paused_str is None:
            return None

        paused_until: datetime.datetime | None = None
        if paused_str.lower() != "none":
            paused_until = _parse_iso(paused_str)
            if paused_until is None:
                return None

        return BoxStatus(
            checked_in_at=checked_in_at,
            state=state,
            current=current,
            paused_until=paused_until,
        )
    except (ValueError, TypeError, KeyError, IndexError):
        return None


def render_box_alert(
    kind: AlertKind | str,
    owner: str,
    since: datetime.datetime,
) -> tuple[str, str]:
    """Render the issue title and body for an operational box alert."""
    if not _IDENT_RE.match(owner):
        raise ValueError(f"Invalid owner format: {owner!r}")

    try:
        alert_kind = AlertKind(kind)
    except ValueError:
        raise ValueError(f"Invalid alert kind: {kind!r}") from None

    titles = {
        AlertKind.weekly_cap: "Box alert: weekly cap reached",
        AlertKind.login_expired: "Box alert: agy login expired",
        AlertKind.box_silent: "Box alert: box silent",
    }
    title = titles[alert_kind]
    body = f"@{owner}\nSince: {_format_iso(since)}\n"
    return title, body


def render_escalation_issue(
    ref: TicketRef,
    effort: str,
    title_slug: str,
    link: str,
    reason: EscalationReason | str,
    owner: str,
) -> tuple[str, str]:
    """Render the issue title and body for escalating a blocked ticket."""
    if not isinstance(ref, TicketRef):
        raise TypeError(f"Invalid ticket ref: {ref!r}")
    if not _IDENT_RE.match(effort):
        raise ValueError(f"Invalid effort format: {effort!r}")
    if not _IDENT_RE.match(title_slug):
        raise ValueError(f"Invalid title_slug format: {title_slug!r}")
    if not _IDENT_RE.match(owner):
        raise ValueError(f"Invalid owner format: {owner!r}")
    if not link.startswith("https://github.com/") or re.search(r"\s", link):
        raise ValueError(f"Invalid link: {link!r}")

    try:
        escalation_reason = EscalationReason(reason)
    except ValueError:
        raise ValueError(f"Invalid escalation reason: {reason!r}") from None

    title = f"Escalation: {effort}-{ref.number:02d} {title_slug}"
    lines = [
        f"@{owner}",
        f"Ticket: {ref.repo} #{ref.number:02d}",
        f"Link: {link}",
        f"Reason: {escalation_reason.value}",
    ]
    return title, "\n".join(lines) + "\n"


_ESCALATION_TITLE_RE = re.compile(r"^Escalation: ([\w.-]+)-(\d+) (.+)$")


def parse_escalation_issue_title(title: str) -> tuple[str, int, str] | None:
    """Parse an escalation issue title back into (effort, ticket number, slug).

    Ticket 26 (§Escalation issues): each dispatch run closes an open escalation
    issue once its ticket is done or its claim branch is gone, and it identifies
    the ticket only from the fixed title `render_escalation_issue` writes. This is
    the one place that reads that title back, so a run never guesses at an issue
    it did not open itself; anything that does not match the template returns
    `None` rather than a wrong ticket number.
    """
    match = _ESCALATION_TITLE_RE.match(title.strip())
    if not match:
        return None
    effort, number_str, title_slug = match.groups()
    return effort, int(number_str), title_slug
