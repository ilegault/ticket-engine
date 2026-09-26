"""The box-worker loop: runs `BoxCore` forever against live GitHub and local state.

WHY THIS EXISTS
---------------
Ticket 31 (spec §The box loop, §Box status issue and alerts; ADR 0006; ADR 0007
rules 4 and 5) is the thin adapter around the already-built `BoxCore` (ticket 22)
and `box_status` (ticket 20): it is the one place that reads GitHub and the
box's own local files into a `BoxWorld`, hands it to `BoxCore.next_step`, and
carries out whatever step comes back through `LocalWorker` (tickets 09/19/28/
29/30). `BoxLoop.tick()` does exactly one of those cycles; `box-worker` runs it
forever (`box-worker --once` runs it once, for a scripted or test invocation).

**Why the box may keep local state while the dispatcher keeps none.**
AGENTS.md §3 invariant 2 says the *dispatcher* keeps no state of its own: every
dispatch run re-reads GitHub and the Jules API from scratch, because that run
is one of many independent, stateless GitHub Actions invocations with nothing
of its own to lose. The box is a different kind of program: one long-lived
process on one machine, not a fleet of ephemeral runs. The spec (§The box loop)
deliberately gives it two small local files in `logs_dir` — a start ledger
(starts per repo, for the daily cap) and a quota-pause record (first failure
and next retry time) — precisely *because* everything else about the box's
world (claims, PRs, CI, the paused variable) is re-read from GitHub every tick
the same way the dispatcher does. Losing either local file only resets a
counter or a backoff timer; it never desyncs the box from what is actually
true in GitHub, so it does not reintroduce the hazard invariant 2 guards
against (a stateful decision-maker whose local state can drift from the
world). `BoxCore` itself stays pure and reads neither file directly — this
module reads them, folds them into `BoxWorld`, and writes back what
`BoxCore.after_quota_error`/`after_success` compute.

**Why `on_outcome` exists.** `LocalWorker.run_one`'s own quota handling
(ticket 29/30) sleeps and resumes *inside* one call, which can block for a
real, multi-hour quota window in production. The box still needs to see each
`AgyResult` the instant it happens, so it can update the pause record and the
public box-status/alert issues without waiting for `run_one` to return.
`BoxLoop` passes an `on_outcome` observer into `run_one`/`fix_ci` for exactly
this; per `local_worker.py`'s own WHY note, that hook never changes what
`run_one`/`fix_ci` decide.

**Why fix-attempt counts live in memory, not on disk.** Unlike the ledger and
pause record, `BoxLoop` does not persist how many times it has run `FixCI` on
a given PR. A GitHub check-runs response carries no history of prior box
attempts, and the spec's "everything else is re-read from GitHub" principle
means the box does not invent a second, disk-based ledger for this. Losing the
in-memory count (a restart) only lets the box try a few more times than
`max_fix_attempts` strictly allows before the *dispatcher's* own
`EscalatePRAction` — not this loop — becomes the authority (ticket 30's own
docstring says the same of `LocalWorker.fix_ci`).
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import logging.handlers
import os
import pathlib
import time
from collections.abc import Callable

from ticket_engine.box_core import (
    BoxCore,
    BoxPR,
    BoxRepo,
    BoxStep,
    BoxWorld,
    ClaimTicket,
    CloseAlert,
    FixCI,
    RaiseAlert,
    ResumeClaim,
    Wait,
    WriteStatus,
)
from ticket_engine.box_status import (
    AlertKind,
    BoxState,
    BoxStatus,
    TicketRef,
    render_box_alert,
    render_box_status,
)
from ticket_engine.config import RepoConfig, load_repo_config
from ticket_engine.local_config import LocalRepoEntry, LocalWorkerConfig, load_local_config
from ticket_engine.local_worker import LocalWorker, _load_tickets_from_path
from ticket_engine.parser import Ticket, TicketParser

logger = logging.getLogger(__name__)

_STATUS_ISSUE_TITLE = "Box status"
_STATUS_ISSUE_LABEL = "engine:box-status"
_ALERT_ISSUE_LABEL = "engine:box-alert"

_LEDGER_FILENAME = "ledger.json"
_PAUSE_RECORD_FILENAME = "pause_record.json"


def _default_now() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _claim_branch_prefix() -> str:
    return "claim/"


def _ticket_number_from_claim_branch(branch: str) -> int | None:
    """`claim/<effort>/<NN>` -> NN, or None if the branch does not match."""
    parts = branch.split("/")
    if len(parts) != 3 or parts[0] != "claim":
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


class BoxLoop:
    """Builds a `BoxWorld` each tick, asks `BoxCore`, and carries out the step."""

    def __init__(
        self,
        config: LocalWorkerConfig,
        github_client: object,
        local_worker: LocalWorker,
        git_runner: Callable[[list[str], str | None, dict[str, str] | None], tuple[int, str]]
        | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        now_fn: Callable[[], datetime.datetime] | None = None,
        box_core: BoxCore | None = None,
        ticket_loader: Callable[[str], list[Ticket]] | None = None,
    ) -> None:
        self.config = config
        self.github_client = github_client
        self.local_worker = local_worker
        self._git_runner = git_runner
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self._now = now_fn if now_fn is not None else _default_now
        self._core = box_core if box_core is not None else BoxCore()
        self._ticket_loader = (
            ticket_loader
            if ticket_loader is not None
            else lambda path: _load_tickets_from_path(pathlib.Path(path))
        )

        self._logs_dir = pathlib.Path(config.logs_dir)
        self._ledger_path = self._logs_dir / _LEDGER_FILENAME
        self._pause_record_path = self._logs_dir / _PAUSE_RECORD_FILENAME

        # In-process only (see this module's WHY note).
        self._last_status_write: datetime.datetime | None = None
        self._fix_attempts: dict[tuple[str, int], int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tick(self) -> BoxStep:
        """Build the world, ask BoxCore for the next step, and carry it out."""
        world = self._build_world()
        step = self._core.next_step(world)
        self._carry_out(step, world)
        return step

    # ------------------------------------------------------------------
    # World building
    # ------------------------------------------------------------------

    def _build_world(self) -> BoxWorld:
        now = self._now()
        repos = [self._build_repo(entry) for entry in self.config.repos]

        ledger = self._read_ledger()
        starts_24h: dict[str, int] = {}
        cutoff = now - datetime.timedelta(hours=24)
        for entry_dict in ledger:
            started_at = _parse_iso(entry_dict.get("started_at"))
            if started_at is not None and started_at >= cutoff:
                repo_name = str(entry_dict.get("repo", ""))
                starts_24h[repo_name] = starts_24h.get(repo_name, 0) + 1

        pause_record = self._read_pause_record()
        quota_first_failure = _parse_iso(pause_record.get("quota_first_failure"))
        quota_retry_at = _parse_iso(pause_record.get("quota_retry_at"))

        weekly_cap_alert_open = self._find_open_alert(AlertKind.weekly_cap) is not None

        return BoxWorld(
            repos=repos,
            starts_24h=starts_24h,
            concurrency=self.config.concurrency,
            quota_first_failure=quota_first_failure,
            quota_retry_at=quota_retry_at,
            weekly_cap_alert_open=weekly_cap_alert_open,
            last_status_write=self._last_status_write,
            status_interval_minutes=self.config.status_interval_minutes,
            poll_interval_minutes=self.config.poll_interval_minutes,
            weekly_cap_after_hours=self.config.weekly_cap_after_hours,
            weekly_cap_backoff_hours=self.config.weekly_cap_backoff_hours,
            now=now,
        )

    def _build_repo(self, entry: LocalRepoEntry) -> BoxRepo:
        if self._git_runner is not None:
            self._git_runner(
                ["git", "-C", entry.path, "pull", "--ff-only"], entry.path, None
            )
        tickets = self._ticket_loader(entry.path)
        repo_config = (
            load_repo_config(pathlib.Path(entry.path))
            if pathlib.Path(entry.path).is_dir()
            else RepoConfig()
        )

        paused_var = self.github_client.get_repo_variable(entry.repo, "TICKET_ENGINE_PAUSED")
        paused = bool(paused_var) and str(paused_var).strip().lower() not in (
            "0", "false", "no", "",
        )

        claims = self._read_claims(entry, tickets)
        open_prs = self._read_open_prs(entry, tickets, claims)

        return BoxRepo(
            repo=entry.repo,
            tickets=tickets,
            config=repo_config,
            paused=paused,
            claims=claims,
            open_prs=open_prs,
        )

    def _read_claims(self, entry: LocalRepoEntry, tickets: list[Ticket]) -> dict[int, str]:
        tickets_by_number = {t.number: t for t in tickets}
        claims: dict[int, str] = {}
        for branch in self.github_client.list_claim_branches(entry.repo):
            number = _ticket_number_from_claim_branch(branch)
            if number is None:
                continue
            ticket = tickets_by_number.get(number)
            ticket_path = (
                str(ticket.path).replace("\\", "/")
                if ticket is not None and ticket.path
                else None
            )
            if ticket_path is None:
                continue
            try:
                file_data = self.github_client.get_file_contents(
                    entry.repo, ticket_path, ref=branch
                )
            except (OSError, KeyError, RuntimeError) as exc:
                logger.warning(
                    "Failed to read claim branch %s for %s: %s", branch, entry.repo, exc
                )
                continue
            parsed = TicketParser().parse_text(file_data.get("content", ""))
            claims[number] = parsed.claimed_by or ""
        return claims

    def _read_open_prs(
        self,
        entry: LocalRepoEntry,
        tickets: list[Ticket],
        claims: dict[int, str],
    ) -> list[BoxPR]:
        tickets_by_number = {t.number: t for t in tickets}
        open_prs: list[BoxPR] = []
        for number, claimant in claims.items():
            if claimant != "box":
                continue
            ticket = tickets_by_number.get(number)
            if ticket is None:
                continue
            effort = ticket.effort or "phase-1"
            ticket_branch = f"ticket/{effort}-{number:02d}-{ticket.slug}"
            pr_number = self.github_client.find_open_pr(entry.repo, ticket_branch)
            if pr_number is None:
                continue
            checks = self.github_client.list_check_runs(entry.repo, ticket_branch)
            ci_failed = any(conclusion == "failure" for _name, conclusion in checks)
            fix_attempts = self._fix_attempts.get((entry.repo, pr_number), 0)
            open_prs.append(
                BoxPR(
                    ticket_number=number,
                    pr_number=pr_number,
                    ci_failed=ci_failed,
                    fix_attempts=fix_attempts,
                )
            )
        return open_prs

    # ------------------------------------------------------------------
    # Carrying out a step
    # ------------------------------------------------------------------

    def _carry_out(self, step: BoxStep, world: BoxWorld) -> None:
        if isinstance(step, WriteStatus):
            self._write_status(world)
        elif isinstance(step, Wait):
            wait_secs = max((step.until - world.now).total_seconds(), 0.0)
            self._sleep(wait_secs)
        elif isinstance(step, ClaimTicket):
            self._claim_ticket(step)
        elif isinstance(step, ResumeClaim):
            self._resume_claim(step)
        elif isinstance(step, FixCI):
            self._fix_ci(step)
        elif isinstance(step, RaiseAlert):
            self._raise_alert(step.kind)
        elif isinstance(step, CloseAlert):
            self._close_alert(step.kind)
        else:  # pragma: no cover - BoxStep is exhaustive
            msg = f"Unknown BoxStep: {step!r}"
            raise TypeError(msg)

    def _entry_for(self, repo: str) -> LocalRepoEntry:
        for entry in self.config.repos:
            if entry.repo == repo:
                return entry
        msg = f"No configured repo entry for {repo!r}"
        raise ValueError(msg)

    def _ticket_for(self, repo: str, ticket_number: int) -> Ticket:
        entry = self._entry_for(repo)
        for t in self._ticket_loader(entry.path):
            if t.number == ticket_number:
                return t
        msg = f"Ticket {ticket_number:02d} not found in {repo!r}"
        raise ValueError(msg)

    def _make_on_outcome(self, repo: str) -> Callable[[object], None]:
        def on_outcome(result: object) -> None:
            outcome = getattr(result, "outcome", None)
            now = self._now()
            if outcome == "quota":
                self._apply_quota_error(now, getattr(result, "reset_at", None))
            elif outcome == "auth":
                self._raise_alert(AlertKind.login_expired)
            elif getattr(result, "success", False):
                self._apply_success()

        return on_outcome

    def _claim_ticket(self, step: ClaimTicket) -> None:
        entry = self._entry_for(step.repo)
        on_outcome = self._make_on_outcome(step.repo)
        self.local_worker.run_one(entry, step.ticket, on_outcome=on_outcome)
        self._append_ledger(step.repo, step.ticket.number)

    def _resume_claim(self, step: ResumeClaim) -> None:
        entry = self._entry_for(step.repo)
        ticket = self._ticket_for(step.repo, step.ticket_number)
        on_outcome = self._make_on_outcome(step.repo)
        self.local_worker.run_one(entry, ticket, on_outcome=on_outcome)

    def _fix_ci(self, step: FixCI) -> None:
        entry = self._entry_for(step.repo)
        ticket = self._ticket_for(step.repo, step.ticket_number)
        on_outcome = self._make_on_outcome(step.repo)
        self.local_worker.fix_ci(entry, ticket, step.pr_number, on_outcome=on_outcome)
        key = (step.repo, step.pr_number)
        self._fix_attempts[key] = self._fix_attempts.get(key, 0) + 1

    def _apply_quota_error(
        self, now: datetime.datetime, reset_at: datetime.datetime | None
    ) -> None:
        pause_record = self._read_pause_record()
        first_failure = _parse_iso(pause_record.get("quota_first_failure")) or now
        weekly_cap_alert_open = self._find_open_alert(AlertKind.weekly_cap) is not None
        partial_world = self._partial_world_for_quota(now, first_failure, weekly_cap_alert_open)
        new_first_failure, retry_at, alerts = BoxCore.after_quota_error(
            partial_world, reset_at=reset_at
        )
        self._write_pause_record(new_first_failure, retry_at)
        for alert_step in alerts:
            self._carry_out(alert_step, partial_world)

    def _apply_success(self) -> None:
        weekly_cap_alert_open = self._find_open_alert(AlertKind.weekly_cap) is not None
        auth_alert_open = self._find_open_alert(AlertKind.login_expired) is not None
        partial_world = self._partial_world_for_quota(self._now(), None, weekly_cap_alert_open)
        _first_failure, _retry_at, alerts = BoxCore.after_success(partial_world)
        self._write_pause_record(None, None)
        for alert_step in alerts:
            self._carry_out(alert_step, partial_world)
        if auth_alert_open:
            self._close_alert(AlertKind.login_expired)

    def _partial_world_for_quota(
        self,
        now: datetime.datetime,
        quota_first_failure: datetime.datetime | None,
        weekly_cap_alert_open: bool,
    ) -> BoxWorld:
        """A `BoxWorld` with only the fields `after_quota_error`/`after_success` read.

        These two `BoxCore` static methods are pure functions of a handful of
        `BoxWorld` fields (WHY note above); this loop calls them the instant an
        outcome is observed, which can be in the middle of a tick, so it builds
        a minimal snapshot rather than re-running the whole `_build_world`.
        """
        return BoxWorld(
            repos=[],
            starts_24h={},
            concurrency=self.config.concurrency,
            quota_first_failure=quota_first_failure,
            quota_retry_at=None,
            weekly_cap_alert_open=weekly_cap_alert_open,
            last_status_write=self._last_status_write,
            status_interval_minutes=self.config.status_interval_minutes,
            poll_interval_minutes=self.config.poll_interval_minutes,
            weekly_cap_after_hours=self.config.weekly_cap_after_hours,
            weekly_cap_backoff_hours=self.config.weekly_cap_backoff_hours,
            now=now,
        )

    # ------------------------------------------------------------------
    # Status issue and alerts
    # ------------------------------------------------------------------

    def _current_ticket_ref(self, world: BoxWorld) -> TicketRef | None:
        for repo in world.repos:
            tickets_by_number = {t.number: t for t in repo.tickets}
            for number, claimant in sorted(repo.claims.items()):
                if claimant != "box":
                    continue
                t = tickets_by_number.get(number)
                if t is None or not t.is_done():
                    return TicketRef(repo=repo.repo, number=number)
        return None

    def _box_state(self, world: BoxWorld) -> BoxState:
        if self._find_open_alert(AlertKind.login_expired) is not None:
            return BoxState.login_expired
        if world.quota_retry_at is not None and world.now < world.quota_retry_at:
            return (
                BoxState.paused_weekly_cap
                if world.weekly_cap_alert_open
                else BoxState.paused_quota
            )
        if self._current_ticket_ref(world) is not None:
            return BoxState.working
        return BoxState.idle

    def _write_status(self, world: BoxWorld) -> None:
        state = self._box_state(world)
        current = self._current_ticket_ref(world)
        paused_until = (
            world.quota_retry_at
            if state in (BoxState.paused_quota, BoxState.paused_weekly_cap)
            else None
        )
        status = BoxStatus(
            checked_in_at=world.now,
            state=state,
            current=current,
            paused_until=paused_until,
        )
        body = render_box_status(status)

        engine_repo = self.config.engine_repo
        issue_number = self._find_open_status_issue()
        if issue_number is None:
            issue_number = self.github_client.create_issue(
                engine_repo, _STATUS_ISSUE_TITLE, body, [_STATUS_ISSUE_LABEL]
            )
            self.github_client.lock_issue(engine_repo, issue_number)
            self.github_client.pin_issue(engine_repo, issue_number)

        self.github_client.update_issue_body(engine_repo, issue_number, body)
        self._last_status_write = world.now

    def _find_open_status_issue(self) -> int | None:
        issues = self.github_client.list_open_issues(self.config.engine_repo, _STATUS_ISSUE_LABEL)
        for item in issues:
            if isinstance(item, dict) and "number" in item:
                return int(item["number"])
        return None

    def _find_open_alert(self, kind: AlertKind) -> int | None:
        owner = self.config.engine_repo.split("/")[0]
        title, _body = render_box_alert(kind, owner=owner, since=self._now())
        return self.github_client.find_open_issue(
            self.config.engine_repo, _ALERT_ISSUE_LABEL, title
        )

    def _raise_alert(self, kind: AlertKind | str) -> None:
        alert_kind = AlertKind(kind)
        engine_repo = self.config.engine_repo
        owner = engine_repo.split("/")[0]
        title, body = render_box_alert(alert_kind, owner=owner, since=self._now())
        if self.github_client.find_open_issue(engine_repo, _ALERT_ISSUE_LABEL, title) is None:
            self.github_client.create_issue(engine_repo, title, body, [_ALERT_ISSUE_LABEL])

    def _close_alert(self, kind: AlertKind | str) -> None:
        alert_kind = AlertKind(kind)
        number = self._find_open_alert(alert_kind)
        if number is not None:
            self.github_client.close_issue(self.config.engine_repo, number)

    # ------------------------------------------------------------------
    # Ledger and pause record (local files; see this module's WHY note)
    # ------------------------------------------------------------------

    def _read_ledger(self) -> list[dict]:
        if not self._ledger_path.is_file():
            return []
        try:
            data = json.loads(self._ledger_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read ledger %s: %s", self._ledger_path, exc)
            return []
        return data if isinstance(data, list) else []

    def _append_ledger(self, repo: str, ticket_number: int) -> None:
        ledger = self._read_ledger()
        ledger.append(
            {
                "repo": repo,
                "ticket": ticket_number,
                "started_at": _format_iso(self._now()),
            }
        )
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    def _read_pause_record(self) -> dict:
        if not self._pause_record_path.is_file():
            return {}
        try:
            data = json.loads(self._pause_record_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read pause record %s: %s", self._pause_record_path, exc)
            return {}
        return data if isinstance(data, dict) else {}

    def _write_pause_record(
        self,
        quota_first_failure: datetime.datetime | None,
        quota_retry_at: datetime.datetime | None,
    ) -> None:
        record = {
            "quota_first_failure": (
                _format_iso(quota_first_failure) if quota_first_failure is not None else None
            ),
            "quota_retry_at": (
                _format_iso(quota_retry_at) if quota_retry_at is not None else None
            ),
        }
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._pause_record_path.write_text(json.dumps(record), encoding="utf-8")


def _parse_iso(value: object) -> datetime.datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt


def _format_iso(dt: datetime.datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.astimezone(datetime.UTC).isoformat()


def _configure_logging(logs_dir: pathlib.Path) -> None:
    """Route every log record to a rotating file in `logs_dir`, never stdout.

    ADR 0007 rule 4/5: the box posts only fixed-template text to GitHub, and
    full logs (which may contain raw `agy` output) stay on the box.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        logs_dir / "box-worker.log", maxBytes=10_000_000, backupCount=5
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _build_loop(local_cfg: LocalWorkerConfig) -> BoxLoop:
    from ticket_engine.agy import AgyDriver
    from ticket_engine.github import GitHubClient

    token = local_cfg.github_token or os.environ.get("PIPELINE_TOKEN", "")
    github_client = GitHubClient(token=token)
    agy_driver = AgyDriver(
        print_timeout=local_cfg.print_timeout,
        quota_error_patterns=local_cfg.quota_error_patterns,
        auth_error_patterns=local_cfg.auth_error_patterns,
    )
    local_worker = LocalWorker(
        config=local_cfg,
        github_client=github_client,
        agy_driver=agy_driver,
    )
    return BoxLoop(
        config=local_cfg,
        github_client=github_client,
        local_worker=local_worker,
    )


def main(
    argv: list[str] | None = None,
    loop_factory: Callable[[LocalWorkerConfig], BoxLoop] | None = None,
) -> int:
    """Entry point for the `box-worker` console script.

    `loop_factory` is an injectable seam for tests (it defaults to
    `_build_loop`, which wires the real `GitHubClient`, `AgyDriver` and
    `LocalWorker`); the console script never passes it.
    """
    parser = argparse.ArgumentParser(
        prog="box-worker",
        description="Run the box: claim, work and push tickets forever.",
    )
    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help="Path to local worker config TOML (default: ~/.ticket-engine-local.toml)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single tick, then exit.",
    )
    args = parser.parse_args(argv)

    local_cfg = load_local_config(args.config)
    _configure_logging(pathlib.Path(local_cfg.logs_dir))
    factory = loop_factory if loop_factory is not None else _build_loop
    loop = factory(local_cfg)

    if args.once:
        loop.tick()
        return 0

    while True:  # pragma: no cover - exercised via --once in tests
        loop.tick()

    return 0  # pragma: no cover
