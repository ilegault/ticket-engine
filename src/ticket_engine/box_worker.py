"""The box-worker loop: runs the box unattended, tick, act, sleep.

WHY THIS EXISTS
---------------
Ticket 31 (spec §The box loop, §Box status issue and alerts, ADR 0006, ADR 0007
rules 4 and 5) wires the already-built pure `BoxCore` (ticket 22) and
`box_status` renderer (ticket 20) to the real world, the same way
`live_dispatch.py` wires `DispatchCore` for the dispatcher. `BoxLoop.tick()`
builds a `BoxWorld` snapshot from GitHub and the box's local files, asks
`BoxCore.next_step` which step to take, and carries that step out through
`LocalWorker`. `box-worker --once` runs a single tick and exits (used by
tests and by a practice run); with no flag it loops forever.

The box may keep local state the dispatcher never does (AGENTS.md §3
invariant 2 is about the dispatcher, which stays a stateless GitHub Action).
Losing either local file only resets it — the box re-reads its claims, PRs
and CI from GitHub on the very next tick:

- a start ledger (`box_ledger.json`), which counts starts per repo in the
  last 24 hours against `RepoConfig.daily_cap`;
- a quota-pause record (`box_pause.json`): the first quota failure time, the
  next retry time, and whether the weekly-cap alert is already open. This is
  `BoxCore`'s own timeline (`after_quota_error`/`after_success`); the loop
  only persists it across restarts.

`BoxCore` was built (ticket 22) before login state existed anywhere in the
spec's seams, so it has no rule for an `auth` outcome. Rather than reopen a
already-merged pure core for one alert kind, the loop owns login-expired
pacing itself, in memory only (an expired login is the developer's problem to
fix by hand — there is nothing useful to persist about it across a restart):
on `auth` it raises the `login_expired` alert once and pauses every repo for
an hour before trying again, the same shape as `BoxCore`'s quota Wait but
outside the pure core.

Everything the box posts publicly — the pinned status issue and every
alert — goes through `box_status`'s fixed-template renderers (ADR 0007 rule
4). The box's own logs (including any `agy` output) go only to a rotating
file under `LocalWorkerConfig.logs_dir`, never to a GitHub request body.

Operating the box (follow-ups from its first real setup). The box runs as a
windowless scheduled task, so its log file is the only place anything it does
can be seen:

- every tick logs one INFO line naming the step it took (`tick: write
  status`, `tick: wait until …`, `tick: claim owner/repo #09`), so a healthy
  idle box is distinguishable from a stuck one;
- a tick that raises is logged with its traceback (`tick failed`) and then
  re-raised, so the task's restart-on-failure still sees the crash;
- a failed `git pull` of a target clone is logged as a warning rather than
  silently ignored (the tick carries on with the tickets already on disk;
  claims and PRs come from GitHub, so a stale clone cannot double-claim);
- a claim or resume whose `run_one` returns False (refused, or the worktree
  could not be made) waits one poll interval before the next tick, because
  `BoxCore` would otherwise pick the same step again at once (ticket 47);
- every git call has `LocalWorkerConfig.git_timeout_seconds` as its upper
  bound, because a git credential prompt nobody can see would otherwise hang
  the loop forever;
- the GitHub token is `LocalWorkerConfig.github_token`, falling back to the
  `PIPELINE_TOKEN` environment variable. Environment variables do not
  reliably reach a scheduled task, so the config file is the primary home;
- Ticket 57 (ADR 0009 rule 7): the box's config must not list repos, and a
  missing, unreadable or repos-containing config makes box-worker refuse to
  start immediately with exit code 2;
- Ticket 58 (ADR 0009 rule 4; ADR 0010 rules 3, 6, 7): the box reads the repo
  list from engine-repos.toml on the engine repo, clones missing listed
  repos, keeps delisted repos until in-flight work finishes, and honours
  BOX_PAUSED on the engine repo.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import logging
import logging.handlers
import os
import pathlib
import time
from collections.abc import Callable

from ticket_engine.agy import AgyDriver
from ticket_engine.box_core import (
    BoxCore,
    BoxPR,
    BoxRepo,
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
from ticket_engine.github import GitHubClient
from ticket_engine.local_config import (
    BoxConfigError,
    LocalRepoEntry,
    LocalWorkerConfig,
    _default_logs_dir,
    load_box_config,
)
from ticket_engine.local_worker import LocalWorker, _load_tickets_from_path
from ticket_engine.parser import Ticket
from ticket_engine.repo_list import RepoListEntry, fetch_repo_list
from ticket_engine.sonnet import SonnetDriver

logger = logging.getLogger(__name__)

_LEDGER_FILENAME = "box_ledger.json"
_PAUSE_FILENAME = "box_pause.json"
_BOX_REPOS_FILENAME = "box_repos.json"
_LEDGER_WINDOW_HOURS = 24
_AUTH_RETRY_HOURS = 1

GitRunner = Callable[[list[str], str | None, dict[str, str] | None], tuple[int, str]]


def _is_truthy(value: str | None) -> bool:
    """Return True if value represents a truthy string (not '0', 'false', 'no', '')."""
    if not value:
        return False
    return value.strip().lower() not in ("0", "false", "no", "")


class BoxLoop:
    """Ticks the box: builds `BoxWorld`, asks `BoxCore`, carries out the step."""

    def __init__(
        self,
        config: LocalWorkerConfig,
        worker: LocalWorker,
        github_client: GitHubClient,
        box_core: BoxCore | None = None,
        git_runner: GitRunner | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        now_fn: Callable[[], datetime.datetime] | None = None,
        ticket_loader: Callable[[LocalRepoEntry], list[Ticket]] | None = None,
        repo_list_fn: Callable[[], list[RepoListEntry]] | None = None,
    ) -> None:
        self.config = config
        self.worker = worker
        self.github_client = github_client
        self.box_core = box_core if box_core is not None else BoxCore()
        self._repo_list_fn = repo_list_fn
        self._last_repo_list: list[RepoListEntry] = []
        # The default runner looks `_default_git_runner` up at call time and
        # always passes the configured timeout.
        self._git_runner: GitRunner = (
            git_runner
            if git_runner is not None
            else lambda args, cwd, env: _default_git_runner(
                args, cwd, env, timeout=self.config.git_timeout_seconds
            )
        )
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self._now = now_fn if now_fn is not None else _utcnow
        self._ticket_loader = (
            ticket_loader if ticket_loader is not None else self._default_ticket_loader
        )
        self._last_status_write: datetime.datetime | None = None
        self._fix_attempts: dict[tuple[str, int], int] = {}
        self._auth_first_failure: datetime.datetime | None = None
        self._auth_retry_at: datetime.datetime | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Build the world, decide one step, and carry it out.

        Logs one line naming the step, and logs any exception before letting
        it propagate (see the module docstring).
        """
        try:
            self._tick()
        except Exception:
            logger.exception("tick failed")
            raise

    def _tick(self) -> None:
        world = self._build_world()

        if self._auth_retry_at is not None and world.now < self._auth_retry_at:
            step = self.box_core.next_step(world)
            if isinstance(step, WriteStatus):
                logger.info("tick: %s", _describe_step(step))
                self._carry_out(step, world)
            else:
                logger.info("tick: %s", _describe_step(Wait(until=self._auth_retry_at)))
                self._sleep(max((self._auth_retry_at - world.now).total_seconds(), 0.0))
            return

        step = self.box_core.next_step(world)
        logger.info("tick: %s", _describe_step(step))
        self._carry_out(step, world)

    def run_forever(self) -> None:
        """Tick forever. `main`'s `--once` flag is the only way to stop early."""
        while True:
            self.tick()

    # ------------------------------------------------------------------
    # World building
    # ------------------------------------------------------------------

    def _box_repos_path(self) -> pathlib.Path:
        return pathlib.Path(self.config.logs_dir) / _BOX_REPOS_FILENAME

    def _read_box_repos(self) -> list[str]:
        path = self._box_repos_path()
        if not path.is_file():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return [str(item) for item in data]
            return []
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read box repos %s: %s", path, exc)
            return []

    def _record_cloned_repo(self, repo: str) -> None:
        repos = self._read_box_repos()
        if repo not in repos:
            repos.append(repo)
        path = self._box_repos_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(repos), encoding="utf-8")

    def _current_entries(self) -> list[LocalRepoEntry]:
        if self._repo_list_fn is None:
            return list(self.config.repos)

        try:
            raw_entries = self._repo_list_fn()
            self._last_repo_list = raw_entries
        except Exception as exc:  # noqa: BLE001
            logger.warning("repo list unreadable: %s", exc)
            raw_entries = self._last_repo_list

        entries: list[LocalRepoEntry] = []
        active_box_repos: set[str] = set()

        for entry in raw_entries:
            if entry.box:
                active_box_repos.add(entry.repo)
                name = entry.repo.partition("/")[2] if "/" in entry.repo else entry.repo
                path = str(pathlib.Path(self.config.projects_dir) / name)
                entries.append(
                    LocalRepoEntry(path=path, repo=entry.repo, accepting_new=True)
                )

        box_repos = self._read_box_repos()
        seen = set(active_box_repos)
        for repo_name in box_repos:
            if repo_name not in seen:
                seen.add(repo_name)
                name = repo_name.partition("/")[2] if "/" in repo_name else repo_name
                path = str(pathlib.Path(self.config.projects_dir) / name)
                if pathlib.Path(path).is_dir():
                    entries.append(
                        LocalRepoEntry(path=path, repo=repo_name, accepting_new=False)
                    )

        return entries

    def _build_world(self) -> BoxWorld:
        now = self._now()
        repos: list[BoxRepo] = []
        for entry in self._current_entries():
            if self._repo_list_fn is not None and not pathlib.Path(entry.path).is_dir():
                rc, out = self._git_runner(
                    ["git", "clone", f"https://github.com/{entry.repo}.git", entry.path],
                    None,
                    None,
                )
                if rc != 0:
                    logger.warning(
                        "git clone failed for %s (exit %d): %s",
                        entry.repo,
                        rc,
                        out.strip(),
                    )
                    continue
                self._record_cloned_repo(entry.repo)

            rc, out = self._git_runner(
                ["git", "-C", entry.path, "pull", "--ff-only"], entry.path, None
            )
            if rc != 0:
                logger.warning(
                    "git pull failed in %s (exit %d): %s", entry.path, rc, out.strip()
                )
            tickets = self._ticket_loader(entry)
            repo_path = pathlib.Path(entry.path)
            repo_config = load_repo_config(repo_path) if repo_path.is_dir() else RepoConfig()

            paused_var = self.github_client.get_repo_variable(entry.repo, "TICKET_ENGINE_PAUSED")
            paused = _is_truthy(paused_var)

            claims = self.worker.list_box_claims(entry, tickets)
            open_prs = self._collect_open_prs(entry, tickets, claims)

            repos.append(
                BoxRepo(
                    repo=entry.repo,
                    tickets=tickets,
                    config=repo_config,
                    paused=paused,
                    claims=claims,
                    open_prs=open_prs,
                    accepting_new=entry.accepting_new,
                )
            )

        developer_paused_var = self.github_client.get_repo_variable(
            self.config.engine_repo, "BOX_PAUSED"
        )
        developer_paused = _is_truthy(developer_paused_var)

        ledger = self._read_ledger()
        starts_24h = _starts_in_last_24h(ledger, now)
        pause_record = self._read_pause_record()

        return BoxWorld(
            repos=repos,
            starts_24h=starts_24h,
            concurrency=self.config.concurrency,
            quota_first_failure=pause_record["first_failure"],
            quota_retry_at=pause_record["retry_at"],
            weekly_cap_alert_open=pause_record["weekly_cap_alert_open"],
            last_status_write=self._last_status_write,
            status_interval_minutes=self.config.status_interval_minutes,
            poll_interval_minutes=self.config.poll_interval_minutes,
            weekly_cap_after_hours=self.config.weekly_cap_after_hours,
            weekly_cap_backoff_hours=self.config.weekly_cap_backoff_hours,
            now=now,
            developer_paused=developer_paused,
        )

    def _collect_open_prs(
        self,
        entry: LocalRepoEntry,
        tickets: list[Ticket],
        claims: dict[int, str],
    ) -> list[BoxPR]:
        prs: list[BoxPR] = []
        tickets_by_number = {t.number: t for t in tickets}
        for number, claimant in claims.items():
            if claimant != "box":
                continue
            ticket = tickets_by_number.get(number)
            if ticket is None:
                continue
            effort = ticket.effort or "phase-1"
            ticket_branch = f"ticket/{effort}-{ticket.number:02d}-{ticket.slug}"
            pr_number = self.github_client.find_open_pr(entry.repo, ticket_branch)
            if pr_number is None:
                continue
            checks = self.github_client.list_check_runs(entry.repo, ticket_branch)
            ci_failed = any(conclusion == "failure" for _, conclusion in checks)
            fix_attempts = self._fix_attempts.get((entry.repo, number), 0)
            prs.append(
                BoxPR(
                    ticket_number=number,
                    pr_number=pr_number,
                    ci_failed=ci_failed,
                    fix_attempts=fix_attempts,
                )
            )
        return prs

    def _default_ticket_loader(self, entry: LocalRepoEntry) -> list[Ticket]:
        return _load_tickets_from_path(pathlib.Path(entry.path))

    # ------------------------------------------------------------------
    # Carrying out a step
    # ------------------------------------------------------------------

    def _carry_out(self, step: object, world: BoxWorld) -> None:
        if isinstance(step, WriteStatus):
            self._write_status(world)
        elif isinstance(step, Wait):
            self._sleep(max((step.until - world.now).total_seconds(), 0.0))
        elif isinstance(step, FixCI):
            self._do_fix_ci(world, step)
        elif isinstance(step, ResumeClaim):
            self._do_resume(world, step)
        elif isinstance(step, ClaimTicket):
            self._do_claim(world, step)
        elif isinstance(step, RaiseAlert):
            self._raise_alert(step.kind, world.now)
        elif isinstance(step, CloseAlert):
            self._close_alert(step.kind)
        else:
            msg = f"Unknown box step: {step!r}"
            raise TypeError(msg)

    def _entry_for(self, repo: str) -> LocalRepoEntry:
        for entry in self._current_entries():
            if entry.repo == repo:
                return entry
        msg = f"No configured local repo entry for {repo!r}"
        raise ValueError(msg)

    def _ticket_for(self, world: BoxWorld, repo: str, ticket_number: int) -> Ticket:
        for box_repo in world.repos:
            if box_repo.repo == repo:
                for ticket in box_repo.tickets:
                    if ticket.number == ticket_number:
                        return ticket
        msg = f"Ticket {ticket_number:02d} not found in {repo!r}"
        raise ValueError(msg)

    def _do_claim(self, world: BoxWorld, step: ClaimTicket) -> None:
        entry = self._entry_for(step.repo)
        result = self.worker.run_one(entry, step.ticket, box_mode=True)
        self._append_ledger(step.repo, step.ticket.number, world.now)
        if result is False:
            self._wait_after_no_op(world, _describe_step(step))
            return
        self._handle_run_result(result, world)

    def _do_resume(self, world: BoxWorld, step: ResumeClaim) -> None:
        entry = self._entry_for(step.repo)
        ticket = self._ticket_for(world, step.repo, step.ticket_number)
        result = self.worker.run_one(entry, ticket, box_mode=True)
        if result is False:
            self._wait_after_no_op(world, _describe_step(step))
            return
        self._handle_run_result(result, world)

    def _wait_after_no_op(self, world: BoxWorld, what: str) -> None:
        """`run_one` returned False: the claim was refused or the ticket could
        not be started, and `LocalWorker` has logged why. `BoxCore` would pick
        the same step again on the very next tick, so wait one poll interval
        rather than spin against the GitHub API (ticket 47)."""
        logger.warning(
            "%s did nothing; waiting %d minutes before the next tick",
            what,
            world.poll_interval_minutes,
        )
        self._sleep(world.poll_interval_minutes * 60)

    def _do_fix_ci(self, world: BoxWorld, step: FixCI) -> None:
        entry = self._entry_for(step.repo)
        ticket = self._ticket_for(world, step.repo, step.ticket_number)
        key = (step.repo, step.ticket_number)
        self._fix_attempts[key] = self._fix_attempts.get(key, 0) + 1
        result = self.worker.fix_ci(entry, ticket, step.pr_number)
        self._handle_run_result(result, world)

    def _handle_run_result(self, result: object, world: BoxWorld) -> None:
        """React to the outcome of a `run_one`/`fix_ci` call.

        `None` means the ticket's claim was lost or it was escalated inside
        `LocalWorker` already — nothing further for the loop to do. Anything
        else is an `AgyResult`-shaped object: `success` clears any quota
        pause and the login-expired alert; `outcome == "quota"` hands off to
        `BoxCore.after_quota_error`; `outcome == "auth"` raises the
        login-expired alert and pauses an hour (see the module docstring).
        Any other non-success outcome (`waiting`, `timeout`, `failed`) was
        already resumed or escalated inside `LocalWorker` — box_mode only
        ever returns those once they are final.
        """
        if result is None:
            return

        if getattr(result, "success", False):
            first_failure, retry_at, steps = BoxCore.after_success(world)
            self._write_pause_record(first_failure, retry_at, False)
            for s in steps:
                self._carry_out(s, world)
            self._clear_auth_alert()
            return

        outcome = getattr(result, "outcome", None)
        if outcome == "quota":
            first_failure, retry_at, steps = BoxCore.after_quota_error(
                world, reset_at=getattr(result, "reset_at", None)
            )
            weekly_cap_open = world.weekly_cap_alert_open or any(
                isinstance(s, RaiseAlert) for s in steps
            )
            self._write_pause_record(first_failure, retry_at, weekly_cap_open)
            for s in steps:
                self._carry_out(s, world)
            return

        if outcome == "auth":
            self._raise_login_expired(world.now)
            return

    # ------------------------------------------------------------------
    # Status issue
    # ------------------------------------------------------------------

    def _write_status(self, world: BoxWorld) -> None:
        repo = self.config.engine_repo
        label = "engine:box-status"
        issue_number = self.github_client.find_open_issue(repo, label, "Box status")
        if issue_number is None:
            issue_number = self.github_client.create_issue(repo, "Box status", "", [label])
            self.github_client.lock_issue(repo, issue_number)
            self.github_client.pin_issue(repo, issue_number)

        status = self._current_box_status(world)
        self.github_client.update_issue_body(repo, issue_number, render_box_status(status))
        self._last_status_write = world.now

    def _current_box_status(self, world: BoxWorld) -> BoxStatus:
        current: TicketRef | None = None
        for repo in world.repos:
            tickets_by_number = {t.number: t for t in repo.tickets}
            box_numbers = sorted(
                num for num, claimant in repo.claims.items() if claimant == "box"
            )
            for number in box_numbers:
                ticket = tickets_by_number.get(number)
                if ticket is None or not ticket.is_done():
                    current = TicketRef(repo=repo.repo, number=number)
                    break
            if current is not None:
                break

        if self._auth_first_failure is not None:
            return BoxStatus(checked_in_at=world.now, state=BoxState.login_expired)

        if world.developer_paused:
            return BoxStatus(
                checked_in_at=world.now,
                state=BoxState.paused_by_developer,
                current=current,
            )

        if world.quota_retry_at is not None and world.now < world.quota_retry_at:
            state = (
                BoxState.paused_weekly_cap
                if world.weekly_cap_alert_open
                else BoxState.paused_quota
            )
            return BoxStatus(
                checked_in_at=world.now,
                state=state,
                current=current,
                paused_until=world.quota_retry_at,
            )

        state = BoxState.working if current is not None else BoxState.idle
        return BoxStatus(checked_in_at=world.now, state=state, current=current)

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    def _raise_alert(self, kind: AlertKind, since: datetime.datetime) -> None:
        repo = self.config.engine_repo
        owner = repo.split("/")[0]
        title, body = render_box_alert(kind, owner, since)
        if self.github_client.find_open_issue(repo, "engine:box-alert", title) is None:
            self.github_client.create_issue(repo, title, body, ["engine:box-alert"])

    def _close_alert(self, kind: AlertKind) -> None:
        repo = self.config.engine_repo
        owner = repo.split("/")[0]
        title, _ = render_box_alert(kind, owner, self._now())
        existing = self.github_client.find_open_issue(repo, "engine:box-alert", title)
        if existing is not None:
            self.github_client.close_issue(repo, existing)

    def _raise_login_expired(self, now: datetime.datetime) -> None:
        if self._auth_first_failure is None:
            self._auth_first_failure = now
        self._auth_retry_at = now + datetime.timedelta(hours=_AUTH_RETRY_HOURS)
        self._raise_alert(AlertKind.login_expired, self._auth_first_failure)

    def _clear_auth_alert(self) -> None:
        if self._auth_first_failure is not None:
            self._close_alert(AlertKind.login_expired)
            self._auth_first_failure = None
            self._auth_retry_at = None

    # ------------------------------------------------------------------
    # Local state: ledger and quota-pause record
    # ------------------------------------------------------------------

    def _ledger_path(self) -> pathlib.Path:
        return pathlib.Path(self.config.logs_dir) / _LEDGER_FILENAME

    def _pause_path(self) -> pathlib.Path:
        return pathlib.Path(self.config.logs_dir) / _PAUSE_FILENAME

    def _read_ledger(self) -> list[dict[str, str]]:
        path = self._ledger_path()
        if not path.is_file():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read box ledger %s: %s", path, exc)
            return []

    def _append_ledger(self, repo: str, ticket_number: int, started_at: datetime.datetime) -> None:
        path = self._ledger_path()
        ledger = self._read_ledger()
        ledger.append(
            {
                "repo": repo,
                "ticket": ticket_number,
                "started_at": started_at.astimezone(datetime.UTC).isoformat(),
            }
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(ledger), encoding="utf-8")

    def _read_pause_record(self) -> dict[str, object]:
        path = self._pause_path()
        default: dict[str, object] = {
            "first_failure": None,
            "retry_at": None,
            "weekly_cap_alert_open": False,
        }
        if not path.is_file():
            return default
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read box pause record %s: %s", path, exc)
            return default
        return {
            "first_failure": _parse_iso(data.get("first_failure")),
            "retry_at": _parse_iso(data.get("retry_at")),
            "weekly_cap_alert_open": bool(data.get("weekly_cap_alert_open", False)),
        }

    def _write_pause_record(
        self,
        first_failure: datetime.datetime | None,
        retry_at: datetime.datetime | None,
        weekly_cap_alert_open: bool,
    ) -> None:
        path = self._pause_path()
        data = {
            "first_failure": first_failure.astimezone(datetime.UTC).isoformat()
            if first_failure is not None
            else None,
            "retry_at": retry_at.astimezone(datetime.UTC).isoformat()
            if retry_at is not None
            else None,
            "weekly_cap_alert_open": weekly_cap_alert_open,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _parse_iso(value: object) -> datetime.datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        dt = datetime.datetime.fromisoformat(value)
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=datetime.UTC)


def _starts_in_last_24h(
    ledger: list[dict[str, str]], now: datetime.datetime
) -> dict[str, int]:
    counts: dict[str, int] = {}
    cutoff = now - datetime.timedelta(hours=_LEDGER_WINDOW_HOURS)
    for record in ledger:
        started_at = _parse_iso(record.get("started_at"))
        if started_at is None or started_at < cutoff:
            continue
        repo = str(record.get("repo", ""))
        counts[repo] = counts.get(repo, 0) + 1
    return counts


def _default_git_runner(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> tuple[int, str]:
    """Run one git command. A timeout is reported as a non-zero result.

    On timeout the child is killed and (124, "... timed out after Ns") is
    returned, so the caller's ordinary failure handling covers it.
    """
    import subprocess

    merged_env = {**os.environ, **env} if env else None
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, cwd=cwd, env=merged_env,
            check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"git command timed out after {timeout}s"
    return result.returncode, result.stdout or result.stderr


def _describe_step(step: object) -> str:
    """One short, fixed-shape phrase for a step, for the per-tick log line."""
    if isinstance(step, WriteStatus):
        return "write status"
    if isinstance(step, Wait):
        return "wait until " + step.until.astimezone(datetime.UTC).strftime("%Y-%m-%dT%H:%MZ")
    if isinstance(step, ClaimTicket):
        return f"claim {step.repo} #{step.ticket.number:02d}"
    if isinstance(step, ResumeClaim):
        return f"resume {step.repo} #{step.ticket_number:02d}"
    if isinstance(step, FixCI):
        return f"fix CI {step.repo} #{step.ticket_number:02d} (PR {step.pr_number})"
    if isinstance(step, RaiseAlert):
        return f"raise alert {step.kind.value}"
    if isinstance(step, CloseAlert):
        return f"close alert {step.kind.value}"
    return type(step).__name__


def _configure_logging(logs_dir: str) -> None:
    """Route every log record to a rotating file under `logs_dir`, never stdout.

    ADR 0007 rule 4: nothing the box does — including raw `agy` output — is
    ever printed. `RotatingFileHandler` keeps the box's full logs local so the
    developer can read them over remote desktop without anything reaching a
    public log.
    """
    path = pathlib.Path(logs_dir)
    path.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        path / "box-worker.log", maxBytes=10_000_000, backupCount=5, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def build_loop(config: LocalWorkerConfig) -> BoxLoop:
    # The config file's token first; PIPELINE_TOKEN only as a fallback. The
    # resolved token is written back into the config so the worker's pushes
    # (which read config.github_token) use the same one as the API client.
    token = config.github_token or os.environ.get("PIPELINE_TOKEN", "")
    config = dataclasses.replace(config, github_token=token)
    github_client = GitHubClient(token=token)
    agy_driver = AgyDriver(
        print_timeout=config.print_timeout,
        quota_error_patterns=config.quota_error_patterns,
        auth_error_patterns=config.auth_error_patterns,
    )
    sonnet_driver = (
        SonnetDriver(timeout_seconds=config.sonnet_timeout_seconds)
        if config.sonnet_enabled
        else None
    )
    worker = LocalWorker(
        config=config,
        github_client=github_client,
        agy_driver=agy_driver,
        sonnet_driver=sonnet_driver,
    )
    return BoxLoop(
        config=config,
        worker=worker,
        github_client=github_client,
        repo_list_fn=lambda: fetch_repo_list(github_client, config.engine_repo),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="box-worker",
        description="Run the box unattended: tick, act, sleep.",
    )
    parser.add_argument(
        "--config", default=None, metavar="PATH",
        help="Path to local worker config TOML (default: ~/.ticket-engine-local.toml)",
    )
    parser.add_argument(
        "--once", action="store_true", help="Run a single tick, then exit."
    )
    args = parser.parse_args(argv)

    try:
        local_cfg = load_box_config(args.config)
    except BoxConfigError as exc:
        _configure_logging(_default_logs_dir())
        logger.error("box-worker refuses to start: %s", exc)
        return 2

    _configure_logging(local_cfg.logs_dir)

    loop = build_loop(local_cfg)

    if args.once:
        loop.tick()
        return 0

    loop.run_forever()
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
