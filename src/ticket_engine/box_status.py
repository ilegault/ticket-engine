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

Ticket 33 (§Held changes) adds `silent_check_action`: the pure decision behind
the scheduled box-silent check. It takes the parsed status, the clock, the
configured `box_silent_hours`, and whether the "Box alert: box silent" issue is
already open, and returns which action (if any) the calling script should take.
It reads no clock and does no I/O itself; `scripts/check_box_silent.py` supplies
`now` and carries out the action.

Ticket 56 (ADR 0010 rules 4–6) adds `paused_by_developer` to `BoxState`,
`NotReadyReason`, `NotReady`, not-ready repo reporting in `BoxStatus` /
`render_box_status` / `parse_box_status`, and `render_repo_not_ready_alert`.

Ticket 77 (ADR 0007 rule 4) reshapes the status body: the times are Central time
(`display_time`), and it gains `Step` (what the box is doing right now, from a fixed
vocabulary), `Last PR` and `Started (24h)`. `parse_box_status` still reads the
old UTC body (no `Step`/`Last PR`/`Started` lines), because the dispatcher, the
morning report and the box-silent check may run a different engine version than
the box that wrote the issue.

Ticket 78 (ADR 0007 rule 4) puts the alert `Since:` lines in Central time too.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from enum import Enum

from ticket_engine.display_time import format_display, parse_display

_REPO_RE = re.compile(r"^[\w.-]+/[\w.-]+$")
_IDENT_RE = re.compile(r"^[\w.-]+$")
_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?Z$")
_STEP_RE = re.compile(
    r"^(?:none|implementing|waiting for quota"
    r"|pre-push gate red \(resume \d+/\d+\)|fixing CI \(\d+/\d+\))$"
)
_LAST_PR_RE = re.compile(r"^([\w.-]+/[\w.-]+) #(\d+), PR #(\d+), (.+)$")
_STARTS_ITEM_RE = re.compile(r"^([\w.-]+/[\w.-]+) (\d+)/(\d+)$")

__all__ = [
    "AlertKind",
    "BoxState",
    "BoxStatus",
    "EscalationReason",
    "LastPR",
    "NotReady",
    "NotReadyReason",
    "TicketRef",
    "parse_box_status",
    "parse_escalation_issue_title",
    "render_box_alert",
    "render_box_status",
    "render_escalation_issue",
    "render_repo_not_ready_alert",
    "silent_check_action",
]


class BoxState(str, Enum):
    """Lifecycle and operational state of the box worker."""

    working = "working"
    idle = "idle"
    paused_quota = "paused_quota"
    paused_weekly_cap = "paused_weekly_cap"
    login_expired = "login_expired"
    paused_by_developer = "paused_by_developer"


class NotReadyReason(str, Enum):
    """Categorical reasons why a target repo is not ready for the box."""

    clone_failed = "clone_failed"
    no_token_access = "no_token_access"
    no_engine_config = "no_engine_config"
    env_failed = "env_failed"
    baseline_red = "baseline_red"


@dataclass(frozen=True)
class NotReady:
    """A target repository that the box will not work, and the categorical reason why."""

    repo: str
    reason: NotReadyReason

    def __post_init__(self) -> None:
        if not _REPO_RE.match(self.repo):
            raise ValueError(f"Invalid repository reference: {self.repo!r}")
        if isinstance(self.reason, str) and not isinstance(self.reason, NotReadyReason):
            object.__setattr__(self, "reason", NotReadyReason(self.reason))


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
class LastPR:
    """The most recent pull request the box opened, and when."""

    ref: TicketRef
    pr_number: int
    opened_at: datetime.datetime

    def __post_init__(self) -> None:
        if self.opened_at.tzinfo is None:
            raise ValueError("opened_at must be timezone-aware (UTC)")
        object.__setattr__(
            self,
            "opened_at",
            self.opened_at.astimezone(datetime.UTC).replace(second=0, microsecond=0),
        )


@dataclass(frozen=True)
class BoxStatus:
    """Snapshot of the box worker's current operational heartbeat.

    `step` is one of `none`, `implementing`, `waiting for quota`,
    `pre-push gate red (resume <n>/<m>)`, `fixing CI (<n>/<m>)`. `starts` is
    `(repo, starts in the last 24 h, daily_cap)` per repo.
    """

    checked_in_at: datetime.datetime
    state: BoxState
    current: TicketRef | None = None
    paused_until: datetime.datetime | None = None
    not_ready: tuple[NotReady, ...] = ()
    step: str = "none"
    last_pr: LastPR | None = None
    starts: tuple[tuple[str, int, int], ...] = ()

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

        if not isinstance(self.not_ready, tuple):
            object.__setattr__(self, "not_ready", tuple(self.not_ready))

        if not isinstance(self.step, str) or not _STEP_RE.match(self.step):
            raise ValueError(f"Invalid box step: {self.step!r}")

        starts = tuple((str(r), int(n), int(cap)) for r, n, cap in self.starts)
        for repo, _n, _cap in starts:
            if not _REPO_RE.match(repo):
                raise ValueError(f"Invalid repository reference: {repo!r}")
        object.__setattr__(self, "starts", starts)


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
        format_display(s.paused_until) if s.paused_until is not None else "none"
    )
    last_pr_str = (
        f"{s.last_pr.ref.repo} #{s.last_pr.ref.number:02d}, PR #{s.last_pr.pr_number}, "
        f"{format_display(s.last_pr.opened_at)}"
        if s.last_pr is not None
        else "none"
    )
    starts_str = (
        ", ".join(f"{repo} {n}/{cap}" for repo, n, cap in s.starts) if s.starts else "none"
    )
    not_ready_str = (
        ", ".join(f"{nr.repo} ({nr.reason.value})" for nr in s.not_ready)
        if s.not_ready
        else "none"
    )
    lines = [
        "## Box status",
        f"Checked in: {format_display(s.checked_in_at)}",
        f"State: {s.state.value}",
        f"Current: {current_str}",
        f"Step: {s.step}",
        f"Last PR: {last_pr_str}",
        f"Started (24h): {starts_str}",
        f"Paused until: {paused_str}",
        f"Not ready: {not_ready_str}",
    ]
    return "\n".join(lines) + "\n"


def _parse_time(text: str) -> datetime.datetime | None:
    """Read a status time in Central display form or the old UTC form."""
    return parse_display(text) or _parse_iso(text)


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

        checked_in_at = _parse_time(data["Checked in"])
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
            paused_until = _parse_time(paused_str)
            if paused_until is None:
                return None

        step = data.get("Step", "none")
        if not _STEP_RE.match(step):
            return None

        last_pr: LastPR | None = None
        last_pr_str = data.get("Last PR", "none")
        if last_pr_str.lower() != "none":
            m = _LAST_PR_RE.match(last_pr_str)
            if not m:
                return None
            opened_at = _parse_time(m.group(4))
            if opened_at is None:
                return None
            last_pr = LastPR(
                ref=TicketRef(repo=m.group(1), number=int(m.group(2))),
                pr_number=int(m.group(3)),
                opened_at=opened_at,
            )

        starts: list[tuple[str, int, int]] = []
        starts_str = data.get("Started (24h)", "none")
        if starts_str.lower() != "none":
            for item in starts_str.split(","):
                m = _STARTS_ITEM_RE.match(item.strip())
                if not m:
                    return None
                starts.append((m.group(1), int(m.group(2)), int(m.group(3))))

        not_ready: tuple[NotReady, ...] = ()
        if "Not ready" in data and data["Not ready"].lower() != "none":
            raw_not_ready = data["Not ready"]
            if not raw_not_ready:
                return None
            entries: list[NotReady] = []
            for item in raw_not_ready.split(","):
                item = item.strip()
                if not item:
                    return None
                m = re.match(r"^([\w.-]+/[\w.-]+)\s*\(([\w_]+)\)$", item)
                if not m:
                    return None
                repo_part, reason_part = m.groups()
                if not _REPO_RE.match(repo_part):
                    return None
                try:
                    reason = NotReadyReason(reason_part)
                except ValueError:
                    return None
                entries.append(NotReady(repo=repo_part, reason=reason))
            not_ready = tuple(entries)

        return BoxStatus(
            checked_in_at=checked_in_at,
            state=state,
            current=current,
            paused_until=paused_until,
            not_ready=not_ready,
            step=step,
            last_pr=last_pr,
            starts=tuple(starts),
        )
    except (ValueError, TypeError, KeyError, IndexError):
        return None


def render_repo_not_ready_alert(
    repo: str,
    reason: NotReadyReason | str,
    owner: str,
    since: datetime.datetime,
) -> tuple[str, str]:
    """Render the issue title and body for a per-repo not-ready alert."""
    if not _REPO_RE.match(repo):
        raise ValueError(f"Invalid repository reference: {repo!r}")
    if not _IDENT_RE.match(owner):
        raise ValueError(f"Invalid owner format: {owner!r}")

    try:
        not_ready_reason = (
            reason if isinstance(reason, NotReadyReason) else NotReadyReason(reason)
        )
    except ValueError:
        raise ValueError(f"Invalid not-ready reason: {reason!r}") from None

    title = f"Box alert: {repo} not ready"
    body = f"@{owner}\nReason: {not_ready_reason.value}\nSince: {format_display(since)}\n"
    return title, body


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
    body = f"@{owner}\nSince: {format_display(since)}\n"
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


def silent_check_action(
    status: BoxStatus | None,
    now: datetime.datetime,
    silent_hours: int,
    alert_open: bool,
) -> str:
    """Decide what the box-silent check should do this run.

    Ticket 33 (§Held changes): pure decision behind the scheduled check that
    alerts the developer when the box goes silent. No I/O, no clock read; the
    caller (`scripts/check_box_silent.py`) supplies `now` and `alert_open` and
    carries out whichever action comes back.

    Returns ``"open"`` when the box is silent (`status is None`, or `now` is at
    least `silent_hours` past `status.checked_in_at`) and no alert is already
    open; ``"close"`` when the box is not silent and an alert is open;
    ``"none"`` otherwise (already alerted while silent, or nothing to do).
    """
    if status is None:
        silent = True
    else:
        now_utc = now if now.tzinfo is not None else now.replace(tzinfo=datetime.UTC)
        silent = now_utc - status.checked_in_at >= datetime.timedelta(hours=silent_hours)

    if silent and not alert_open:
        return "open"
    if not silent and alert_open:
        return "close"
    return "none"
