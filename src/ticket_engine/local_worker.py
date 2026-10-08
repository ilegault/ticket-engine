"""Local worker orchestrator — claims any ticket and drives agy.

WHY THIS EXISTS
---------------
Ticket 09 introduced the local worker for Runner: windows tickets.
Ticket 28 (spec §Local worker orchestration, ADR 0006 rules 2 and 4, ADR 0007
rule 3) extends it to work any ticket and to push honestly:

- Claiming commits Claimed-by: box to the ticket file on the claim branch
  (ADR 0006 rule 4), reusing the insert_claimed_by helper from ticket 21.
- The ticket branch is created from the claim branch head, so the Claimed-by
  line travels into the PR.
- A background CheckpointPusher pushes every checkpoint_push_minutes while
  agy runs. A final push happens after every run, regardless of outcome.
- The GitHub PAT is passed only through per-process git environment variables
  (GIT_CONFIG_VALUE_0=AUTHORIZATION: basic …). It never appears in git args
  or logs (ADR 0007 rule 3).
- A worktree with unpushed commits is never removed with --force. Only a
  clean, fully-pushed worktree is removed.

Ticket 29 (spec §Local worker orchestration, ADR 0006 rule 7) extends this
further:

- A finished run (worktree ticket file at Status: done) opens the ticket's PR
  through the GitHub API, base the repo's default branch, title
  `<effort>-<NN>: <title>`, a fixed body. At most one PR per ticket branch:
  `find_open_pr` is checked first.
- Before every resume, push and PR, the orchestrator re-reads the claim
  branch (`claim_still_mine`). If the claim branch is gone, or its ticket file
  now reads `Claimed-by: jules`, the box has lost the ticket: it pushes and
  PRs nothing more, force-removes its local worktree (the one place --force is
  used, because the ticket is no longer the box's to keep), leaves the remote
  ticket branch alone, and logs the loss.

Ticket 31 (spec §The box loop, §Quota protocol) adds two things the box-worker
loop (`box_worker.py`) needs and `work-windows` must never see:

- `list_box_claims`: reads every `claim/<effort>/<NN>` branch's `Claimed-by`
  line, so `box_worker.BoxLoop` can build a `BoxWorld` without duplicating the
  claim-branch/`Claimed-by` reading `_claim_still_mine` already does.
- `run_one(..., box_mode=True)`: `work-windows` runs once and is meant to sit
  and wait out a quota pause inside a single `run_one` call (ticket 29's
  behaviour, unchanged: `box_mode` defaults to `False`). The box-worker loop
  cannot do that — it also has to keep rewriting the pinned status issue and
  reacting to alerts while paused, which only happens between ticks. So in
  `box_mode`, a `quota` or `auth` outcome is handed straight back to the
  caller instead of being slept out here; `BoxCore`'s own timeline
  (`after_quota_error`) and the box-worker's login-expired handling own the
  retry pacing instead. Every other outcome (`waiting`, `timeout`, `failed`,
  and their resume/auto-reply/escalation handling) is unchanged in both modes.

Ticket 47 (the box's first real run): resuming has to survive what a crash,
a quota pause or a restart leaves behind.

- A claim branch that already exists and already says `Claimed-by: box` is the
  box's own claim, so `run_one` works it instead of reporting a collision.
  Before this, `BoxCore`'s ResumeClaim could never resume anything: `run_one`
  always tried to create the claim branch first, read "already exists" as
  someone else's claim, and returned at once.
- An existing worktree folder (one with a `.git` entry) is reused as it is.
  Otherwise stale worktree registrations are pruned, and a fresh claim uses
  `worktree add -B`, so a local ticket branch left over from a failed run is
  reset to the claim head rather than making git refuse.
- If git still cannot create the worktree, `run_one` logs git's message and
  returns `False`. agy is never started in a folder that does not exist.

Ticket 60 (ADR 0010 rule 2): `LocalWorker` gains `env_for` to query each repo's
environment and pass it to both `agy_driver` and `sonnet_driver` via
`_start_with_fallback`.

Ticket 70 (spec §Problem Statement, ADR 0011 rule 1): `escalate_fixes` escalates
a PR whose fix attempts are exhausted, creating/reusing the worktree and calling
`_escalate` with `EscalationReason.ci_failed` and commit message
`Escalate <NN>: fix attempts exhausted`.

Ticket 72 (ADR 0011 rule 3, ADR 0007 rules 4 and 5, ADR 0010 rule 2): the
pre-push gate. agy's own local gate is trusted by nobody, so `_publish` runs the
repo's `gate_commands` (every one) in the worktree, inside the repo environment,
before the box opens a PR and before every push to a branch that already has an
open PR. Red means no PR and no push; the run is treated like a failed run (one
resume against `max_resumes_per_ticket`) and the next prompt carries the full
failure report. Pushes to a branch with no PR (the checkpoint timer, an unfinished
ticket) stay ungated, since nothing can merge them. Gate output goes to the box
log only, never to a GitHub request body.

Ticket 73 (ADR 0011 rule 3): once every gate command passes, `_publish` runs the
integrity gate locally (`integrity_runner --local`) against `origin/<default
branch>`, as one more gate entry named `integrity gate (local)`. It runs under the
repo environment's Python, because check 7 runs the PR's new tests with whatever
Python runs the gate; the engine has no dependencies, so its source folder goes on
`PYTHONPATH` rather than being installed into every repo environment. A `fail` blocks
the push like a red command; a `hold` exits 0 and still pushes. It writes nothing
to GitHub.

Ticket 77 (ADR 0007 rule 4, ADR 0010 rule 5): the status issue shows what the box is
doing while agy runs, not only between ticks. `LocalWorker.status_hook` is called with
`implementing` before every implement or resume run, `pre-push gate red (resume n/m)`
when the gate sends agy back, and `fixing CI (n/m)` at the start of `fix_ci` (n is the
count in the fix-attempt ledger, which the box loop bumps before the run). The hook
carries a fixed step word only, never output. Opening a PR also records it in
`box_last_pr.json` in the logs folder, for the status issue's `Last PR` line.
"""
from __future__ import annotations

import base64
import datetime
import json
import logging
import os
import pathlib
import re
import threading
import time
import urllib.error
from collections.abc import Callable

import ticket_engine
from ticket_engine.agy import AgyDriver
from ticket_engine.box_status import EscalationReason, TicketRef, render_escalation_issue
from ticket_engine.config import RepoConfig, load_repo_config
from ticket_engine.dispatch import (
    AUTO_REPLY_TEXT,
    DispatchCore,
    WorldSnapshot,
    apply_escalation_to_ticket_text,
    assemble_escalation_brief,
    insert_claimed_by,
)
from ticket_engine.gate import GateCommandResult, GateResult, format_gate_report, run_gate
from ticket_engine.github import GitHubClient
from ticket_engine.local_config import LocalRepoEntry, LocalWorkerConfig
from ticket_engine.parser import Ticket, TicketParser
from ticket_engine.prompt import (
    assemble_fix_prompt,
    assemble_prompt,
    load_ticket_skill,
)
from ticket_engine.prompt import (
    extract_progress_note as _extract_progress_note,
)
from ticket_engine.repo_env import CommandRunner, default_command_runner, env_paths
from ticket_engine.sonnet import SonnetDriver

logger = logging.getLogger(__name__)


class WorktreeError(RuntimeError):
    """`git worktree add` failed; the message carries git's output (ticket 47)."""

# Local files in `LocalWorkerConfig.logs_dir` shared with the box loop (ticket 77).
FIX_ATTEMPTS_FILENAME = "fix_attempts.json"
LAST_PR_FILENAME = "box_last_pr.json"

# Buffer added to the reset time before resuming (seconds).
_QUOTA_RESET_BUFFER_SECS = 60

# Type alias for the injectable git runner.
# Signature: (args, cwd, env) -> (returncode, output)
GitRunner = Callable[[list[str], str | None, dict[str, str] | None], tuple[int, str]]


class CheckpointPusher:
    """Pushes the ticket branch on a timer while agy runs.

    WHY THIS EXISTS
    ---------------
    ADR 0006 rule and ticket 28 AC2: the orchestrator pushes the branch every
    checkpoint_push_minutes so a reboot or quota pause leaves a recent
    checkpoint on the remote.

    run_until(stop_event) is meant to run in a background thread. It pushes
    once immediately at t=0, then sleeps interval_s, pushes again, and
    repeats until stop_event is set.
    """

    def __init__(
        self,
        push_fn: Callable[[], None],
        interval_s: float,
        sleep_fn: Callable[[float], None] | None = None,
    ) -> None:
        self._push_fn = push_fn
        self._interval_s = interval_s
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep

    def run_until(self, stop_event: threading.Event) -> None:
        """Push at t=0, then every interval_s until stop_event is set."""
        self._push_fn()
        while not stop_event.is_set():
            self._sleep(self._interval_s)
            self._push_fn()


class LocalWorker:
    """Orchestrates finding, claiming, and working a ticket with agy."""

    def __init__(
        self,
        config: LocalWorkerConfig,
        github_client: GitHubClient,
        agy_driver: AgyDriver,
        git_runner: GitRunner | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        skill_text: str | None = None,
        ticket_loader: Callable[[LocalRepoEntry], list[Ticket]] | None = None,
        read_ticket_fn: Callable[[str | pathlib.Path], str] | None = None,
        write_ticket_fn: Callable[[str | pathlib.Path, str], None] | None = None,
        sonnet_driver: SonnetDriver | None = None,
        env_for: Callable[[LocalRepoEntry], dict[str, str] | None] | None = None,
        command_runner: CommandRunner = default_command_runner,
        status_hook: Callable[[str], None] | None = None,
        now_fn: Callable[[], datetime.datetime] | None = None,
    ) -> None:
        self.config = config
        self.github_client = github_client
        self.agy_driver = agy_driver
        self.status_hook = status_hook
        self._now = now_fn if now_fn is not None else _utcnow
        self.sonnet_driver = sonnet_driver
        self._command_runner = command_runner
        self._env_for: Callable[[LocalRepoEntry], dict[str, str] | None] = (
            env_for if env_for is not None else (lambda entry: None)
        )
        # The default runner looks `_default_git_runner` up at call time and
        # always passes the configured timeout (box setup follow-up).
        self._git_runner: GitRunner = (
            git_runner
            if git_runner is not None
            else lambda args, cwd, env: _default_git_runner(
                args, cwd, env, timeout=self.config.git_timeout_seconds
            )
        )
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self._skill_text = skill_text
        self._ticket_loader = (
            ticket_loader if ticket_loader is not None else self._default_ticket_loader
        )
        self._read_ticket = (
            read_ticket_fn if read_ticket_fn is not None else _default_read_ticket
        )
        self._write_ticket = (
            write_ticket_fn if write_ticket_fn is not None else _default_write_ticket
        )

    def _start_with_fallback(
        self, prompt: str, cwd: str, env: dict[str, str] | None = None
    ) -> object:
        """Try agy first; fall back to Sonnet only on an agy quota error.

        Ticket 40 (spec §LocalWorker changes): every agy call site goes
        through this method instead of calling agy_driver.start directly, so
        _resolve_outcome's resume/auto-reply/escalation counters and
        box_mode's return-to-caller pause behaviour only ever see the
        combined result, whichever driver produced it.
        """
        extra = {"env": env} if env is not None else {}
        result = self.agy_driver.start(prompt, cwd=cwd, **extra)
        if result.quota_error and self.sonnet_driver is not None:
            logger.info("agy out of quota; falling back to Sonnet for this attempt.")
            return self.sonnet_driver.start(prompt, cwd=cwd, **extra)
        return result

    def _report_step(self, step: str) -> None:
        """Tell the status hook what the box is doing now (ticket 77)."""
        if self.status_hook is not None:
            self.status_hook(step)

    def _fix_attempt_count(self, repo: str, pr_number: int) -> int:
        """Attempts recorded for this PR in the box loop's fix-attempt ledger."""
        path = pathlib.Path(self.config.logs_dir) / FIX_ATTEMPTS_FILENAME
        if not path.is_file():
            return 0
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return int(data.get(f"{repo}#{pr_number}", {}).get("attempts", 0))
        except (OSError, ValueError, AttributeError, TypeError) as exc:
            logger.warning("Failed to read fix attempts ledger %s: %s", path, exc)
            return 0

    def _record_last_pr(self, repo: str, ticket_number: int, pr_number: int) -> None:
        path = pathlib.Path(self.config.logs_dir) / LAST_PR_FILENAME
        record = {
            "repo": repo,
            "ticket": ticket_number,
            "pr_number": int(pr_number),
            "opened_at": self._now().astimezone(datetime.UTC).isoformat(),
        }
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(record), encoding="utf-8")
        except OSError as exc:
            logger.warning("Failed to record the last PR in %s: %s", path, exc)

    @property
    def skill_text(self) -> str:
        if self._skill_text is None:
            self._skill_text = load_ticket_skill()
        return self._skill_text

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_windows_frontier(self, entry: LocalRepoEntry) -> list[Ticket]:
        """Load tickets from the local clone and return windows tickets on the frontier.

        Uses the dispatch core (pure) to compute the frontier, then returns only
        the tickets with runner=windows. Claims are not checked here; run_one
        checks them via the GitHub API.
        """
        tickets = self._ticket_loader(entry)
        if not tickets:
            return []
        repo_path = pathlib.Path(entry.path)
        repo_config = load_repo_config(repo_path) if repo_path.is_dir() else None
        snapshot = WorldSnapshot(
            tickets=tickets,
            config=repo_config or RepoConfig(),
        )
        result = DispatchCore().evaluate(snapshot)
        return result.skipped_windows_tickets

    def run_one(
        self,
        entry: LocalRepoEntry,
        ticket: Ticket,
        now: datetime.datetime | None = None,
        box_mode: bool = False,
    ) -> bool | object | None:
        """Claim one ticket (any runner), work it in a worktree with agy.

        Steps:
        1. Create claim branch via GitHub API.
        2. Commit Claimed-by: box to the ticket file on the claim branch.
        3. Create a git worktree on the ticket branch (from claim head, or
           resuming an existing ticket branch on the remote).
        4. Start a background CheckpointPusher.
        5. Drive agy.
        6. Stop the pusher; push once more after agy returns regardless of outcome.
        7. On quota error: keep claim and worktree, sleep until reset, resume.
        8. Remove the worktree only when all commits are pushed and the tree is clean.

        Returns True on success, False on refusal (claim collision) or failure.
        """
        effort = ticket.effort or "phase-1"
        ticket_branch = f"ticket/{effort}-{ticket.number:02d}-{ticket.slug}"
        ticket_path = _ticket_path_str(ticket, effort)
        claim_branch = f"claim/{effort}/{ticket.number:02d}"

        # 1. Get default branch SHA and create claim branch.
        try:
            base_sha = self.github_client.get_default_branch_sha(
                entry.repo, self._default_branch(entry)
            )
        except (OSError, ValueError, RuntimeError) as exc:
            logger.error("Failed to get default branch SHA for %s: %s", entry.repo, exc)
            return False

        claimed = self.github_client.create_claim_branch(
            repo=entry.repo,
            effort=effort,
            ticket_number=ticket.number,
            sha=base_sha,
        )
        if claimed:
            # 2. Commit Claimed-by: box to the ticket file on the claim branch.
            self._commit_claimed_by(entry.repo, ticket_path, claim_branch)
        elif self._claim_owner(entry.repo, claim_branch, ticket_path) == "box":
            logger.info(
                "Resuming the box's own claim %s for %s.", claim_branch, entry.repo
            )
        else:
            logger.info(
                "Ticket %02d already claimed for %s, skipping.",
                ticket.number,
                entry.repo,
            )
            return False

        # 3. Create worktree from the claim branch head (or resume existing ticket branch).
        worktree_path = self._worktree_path(entry, ticket)
        try:
            self._create_worktree(entry.path, worktree_path, ticket_branch, claim_branch)
        except WorktreeError as exc:
            logger.error(
                "Ticket %02d not started: could not create worktree %s: %s",
                ticket.number,
                worktree_path,
                exc,
            )
            return False

        # 4-6. Start pusher, run agy, push after every outcome.
        push_env = _make_push_env(self.config.github_token) if self.config.github_token else None
        stop_event = threading.Event()

        def do_push() -> None:
            # The timer never pushes to a branch with an open PR: that push
            # would skip the pre-push gate (ticket 72).
            if self.github_client.find_open_pr(entry.repo, ticket_branch) is not None:
                return
            self._push_if_claimed(
                entry.repo, claim_branch, ticket_path, worktree_path, ticket_branch, push_env
            )

        interval_s = self.config.checkpoint_push_minutes * 60
        pusher = CheckpointPusher(do_push, interval_s, self._sleep)
        pusher_thread = threading.Thread(
            target=pusher.run_until, args=(stop_event,), daemon=True
        )
        pusher_thread.start()

        try:
            self._report_step("implementing")
            prompt = assemble_prompt(self.skill_text, entry.repo, ticket_path)
            result = self._start_with_fallback(
                prompt, cwd=worktree_path, env=self._env_for(entry)
            )
        finally:
            stop_event.set()
            pusher_thread.join(timeout=5)

        # Always publish after every run, whatever the outcome (gated when the
        # ticket is done or a PR is open).
        gate_result = self._publish(
            entry, ticket, effort, ticket_path, ticket_branch, claim_branch,
            worktree_path, push_env,
        )

        result = self._resolve_outcome(
            entry=entry,
            ticket=ticket,
            effort=effort,
            ticket_path=ticket_path,
            ticket_branch=ticket_branch,
            claim_branch=claim_branch,
            worktree_path=worktree_path,
            push_env=push_env,
            result=result,
            box_mode=box_mode,
            gate_result=gate_result,
        )

        if result is None:
            return None if box_mode else False

        if result.success:
            self._cleanup_worktree(entry.path, worktree_path, ticket_branch)
            return result if box_mode else True

        logger.error(
            "agy failed for ticket %02d (outcome: %s)",
            ticket.number,
            result.outcome,
        )
        self._cleanup_worktree(entry.path, worktree_path, ticket_branch)
        return result if box_mode else False

    def fix_ci(
        self, entry: LocalRepoEntry, ticket: Ticket, pr_number: int
    ) -> object | None:
        """Run agy again on the ticket branch with the real failing output.

        Ticket 30, reworked by ticket 71 (ADR 0011 rules 1 and 2): the dispatcher
        and the box's fix-attempt ledger own how many times to try; this method is
        one attempt. It builds `failures` from each failed check run's job-log tail
        plus a local `run_gate` of the repo's `gate_commands` in the worktree, and
        starts agy with `assemble_fix_prompt` (the ticket is already done and
        claimed, so no skill and no claim instructions). The result goes through
        `_resolve_outcome(box_mode=True)` with the fix prompt as `resume_prompt`,
        so a failed or timed-out fix run is resumed with the fix prompt and then
        escalated, and a quota result is returned with the worktree left in place.
        """
        effort = ticket.effort or "phase-1"
        ticket_branch = f"ticket/{effort}-{ticket.number:02d}-{ticket.slug}"
        ticket_path = _ticket_path_str(ticket, effort)
        worktree_path = self._worktree_path(entry, ticket)
        claim_branch = f"claim/{effort}/{ticket.number:02d}"

        logger.info("Fixing CI for ticket %02d PR #%s", ticket.number, pr_number)
        repo_path = pathlib.Path(entry.path)
        cfg = load_repo_config(repo_path) if repo_path.is_dir() else RepoConfig()
        self._report_step(
            f"fixing CI ({self._fix_attempt_count(entry.repo, pr_number)}"
            f"/{cfg.max_fix_attempts})"
        )
        # A finished run removed its worktree, so make (or reuse) it before
        # agy runs in it (ticket 47).
        try:
            self._create_worktree(entry.path, worktree_path, ticket_branch, claim_branch)
        except WorktreeError as exc:
            logger.error(
                "CI fix for ticket %02d not started: could not create worktree %s: %s",
                ticket.number,
                worktree_path,
                exc,
            )
            return None
        failures: list[tuple[str, str]] = []
        for name, job_id in self.github_client.list_failed_check_runs(
            entry.repo, ticket_branch
        ):
            failures.append((name, self.github_client.get_job_log_tail(entry.repo, job_id)))

        gate = run_gate(
            list(cfg.gate_commands),
            worktree_path,
            self._gate_env(entry, cfg),
            self._command_runner,
        )
        for command_result in gate.results:
            if not command_result.passed:
                failures.append(
                    ("local: " + command_result.command, command_result.output_tail)
                )

        fix_prompt = assemble_fix_prompt(entry.repo, ticket_path, failures)
        result = self._start_with_fallback(
            fix_prompt, cwd=worktree_path, env=self._env_for(entry)
        )

        push_env = _make_push_env(self.config.github_token) if self.config.github_token else None
        gate_result = self._publish(
            entry, ticket, effort, ticket_path, ticket_branch, claim_branch,
            worktree_path, push_env,
        )
        # A fix run is resumed, answered, paused or escalated like any other run
        # (ticket 71); before this a failed or timed-out fix run was dropped.
        return self._resolve_outcome(
            entry=entry,
            ticket=ticket,
            effort=effort,
            ticket_path=ticket_path,
            ticket_branch=ticket_branch,
            claim_branch=claim_branch,
            worktree_path=worktree_path,
            push_env=push_env,
            result=result,
            box_mode=True,
            resume_prompt=fix_prompt,
            gate_result=gate_result,
        )

    def escalate_fixes(
        self, entry: LocalRepoEntry, ticket: Ticket, pr_number: int
    ) -> None:
        """Escalate a ticket whose fix attempts on an open PR have run out.

        Makes (or reuses) the worktree for the ticket branch, then delegates to
        `_escalate` with EscalationReason.ci_failed (Ticket 70, ADR 0011).
        """
        effort = ticket.effort or "phase-1"
        ticket_branch = f"ticket/{effort}-{ticket.number:02d}-{ticket.slug}"
        ticket_path = _ticket_path_str(ticket, effort)
        worktree_path = self._worktree_path(entry, ticket)
        claim_branch = f"claim/{effort}/{ticket.number:02d}"

        logger.info(
            "Escalating fixes for ticket %02d PR #%s", ticket.number, pr_number
        )
        try:
            self._create_worktree(entry.path, worktree_path, ticket_branch, claim_branch)
        except WorktreeError as exc:
            logger.error(
                "Escalate fixes for ticket %02d not started: could not create worktree %s: %s",
                ticket.number,
                worktree_path,
                exc,
            )
            return

        push_env = _make_push_env(self.config.github_token) if self.config.github_token else None
        self._escalate(
            entry=entry,
            ticket=ticket,
            effort=effort,
            ticket_path=ticket_path,
            ticket_branch=ticket_branch,
            worktree_path=worktree_path,
            push_env=push_env,
            reason=EscalationReason.ci_failed,
        )

    def _resolve_outcome(
        self,
        entry: LocalRepoEntry,
        ticket: Ticket,
        effort: str,
        ticket_path: str,
        ticket_branch: str,
        claim_branch: str,
        worktree_path: str,
        push_env: dict[str, str] | None,
        result: object,
        box_mode: bool = False,
        resume_prompt: str | None = None,
        gate_result: GateResult | None = None,
    ) -> object | None:
        """Classify each agy outcome and decide whether to resume, answer, or escalate.

        Ticket 30 (spec §Local worker orchestration, ADR 0004):
        - `quota` never counts toward anything: it waits for the reset and
          resumes, however many times it recurs (ticket 29 behaviour, unchanged).
        - `waiting` gets a fresh auto-reply run, up to
          `RepoConfig.max_auto_replies` times; the next `waiting` past that
          escalates with reason `kept_asking`.
        - `timeout`/`failed` resumes from checkpoint, up to
          `LocalWorkerConfig.max_resumes_per_ticket` times; the run that
          reaches the bound escalates with reason `resumes_exhausted`.

        Ticket 31: in `box_mode`, a `quota` or `auth` outcome is returned to
        the caller immediately rather than slept out here (see the module
        docstring). The claim and worktree are left exactly as they are —
        the box-worker loop resumes them on a later tick via `ResumeClaim`.

        Ticket 72: a success whose pre-push gate (`gate_result`) is red is treated
        like a `failed` run: one resume against `max_resumes_per_ticket`, and the
        next prompt is the previous prompt plus the gate report.

        Returns the final `AgyResult` (success or exhausted-but-not-escalated),
        or `None` once escalation or a lost claim has already settled the run.
        """
        repo_path = pathlib.Path(entry.path)
        repo_config = load_repo_config(repo_path) if repo_path.is_dir() else None
        cfg = repo_config or RepoConfig()

        resumes = 0
        auto_replies = 0

        while not result.success or (gate_result is not None and not gate_result.passed):
            if result.quota_error:
                if box_mode:
                    return result

                if not self._claim_still_mine(entry.repo, claim_branch, ticket_path):
                    logger.info(
                        "Lost claim for ticket %02d (%s); dropping worktree without resuming.",
                        ticket.number,
                        claim_branch,
                    )
                    self._force_remove_worktree(entry.path, worktree_path)
                    return None

                wait_secs = _seconds_until_reset(getattr(result, "reset_at", None))
                logger.info(
                    "Quota error for ticket %02d. Waiting %.0f seconds for quota reset.",
                    ticket.number,
                    wait_secs,
                )
                self._sleep(wait_secs)
                self._report_resume_step(resume_prompt, gate_result)
                result = self._resume_from_checkpoint(
                    entry, ticket_path, worktree_path, resume_prompt, _gate_suffix(gate_result)
                )
                gate_result = self._publish(
                    entry, ticket, effort, ticket_path, ticket_branch, claim_branch,
                    worktree_path, push_env,
                )
                continue

            if box_mode and result.outcome == "auth":
                return result

            if result.outcome == "waiting":
                if auto_replies < cfg.max_auto_replies:
                    auto_replies += 1
                    self._report_resume_step(resume_prompt, gate_result)
                    result = self._send_auto_reply(
                        entry, ticket_path, worktree_path, resume_prompt
                    )
                    gate_result = self._publish(
                        entry, ticket, effort, ticket_path, ticket_branch, claim_branch,
                        worktree_path, push_env,
                    )
                    continue

                self._escalate(
                    entry=entry,
                    ticket=ticket,
                    effort=effort,
                    ticket_path=ticket_path,
                    ticket_branch=ticket_branch,
                    worktree_path=worktree_path,
                    push_env=push_env,
                    reason=EscalationReason.kept_asking,
                )
                return None

            # "timeout" or "failed"
            resumes += 1
            if resumes >= self.config.max_resumes_per_ticket:
                self._escalate(
                    entry=entry,
                    ticket=ticket,
                    effort=effort,
                    ticket_path=ticket_path,
                    ticket_branch=ticket_branch,
                    worktree_path=worktree_path,
                    push_env=push_env,
                    reason=EscalationReason.resumes_exhausted,
                )
                return None

            if gate_result is not None and not gate_result.passed:
                self._report_step(
                    f"pre-push gate red (resume {resumes}/{self.config.max_resumes_per_ticket})"
                )
            else:
                self._report_resume_step(resume_prompt, gate_result)
            result = self._resume_from_checkpoint(
                entry, ticket_path, worktree_path, resume_prompt, _gate_suffix(gate_result)
            )
            gate_result = self._publish(
                entry, ticket, effort, ticket_path, ticket_branch, claim_branch,
                worktree_path, push_env,
            )

        return result

    def _report_resume_step(
        self, resume_prompt: str | None, gate_result: GateResult | None
    ) -> None:
        """Report `implementing` before a resume of an implement run.

        A fix run (`resume_prompt` set) keeps its `fixing CI` step; a run sent back
        by a red gate reports the gate step instead (ticket 77).
        """
        if resume_prompt is None and (gate_result is None or gate_result.passed):
            self._report_step("implementing")

    def _resume_from_checkpoint(
        self,
        entry: LocalRepoEntry,
        ticket_path: str,
        worktree_path: str,
        resume_prompt: str | None = None,
        suffix: str = "",
    ) -> object:
        """Start a fresh agy session that includes the ticket's checkpoint progress note.

        With `resume_prompt` (a fix run, ticket 71) that prompt is used as is: a
        fix run resumes with the fix prompt, never the implement-from-scratch one.
        `suffix` (ticket 72) is the pre-push gate report, appended to either prompt.
        """
        if resume_prompt is not None:
            return self._start_with_fallback(
                resume_prompt + suffix, cwd=worktree_path, env=self._env_for(entry)
            )
        progress_note = self._read_progress_note(worktree_path, ticket_path)
        checkpoint_prompt = _assemble_checkpoint_prompt(
            self.skill_text, entry.repo, ticket_path, progress_note
        )
        return self._start_with_fallback(
            checkpoint_prompt + suffix, cwd=worktree_path, env=self._env_for(entry)
        )

    def _send_auto_reply(
        self,
        entry: LocalRepoEntry,
        ticket_path: str,
        worktree_path: str,
        resume_prompt: str | None = None,
    ) -> object:
        """Start a fresh agy session whose prompt ends with `dispatch.AUTO_REPLY_TEXT`
        (ADR 0004: answer a waiting worker exactly as the engine answers Jules)."""
        prompt = (
            resume_prompt
            if resume_prompt is not None
            else assemble_prompt(self.skill_text, entry.repo, ticket_path)
        )
        return self._start_with_fallback(
            f"{prompt}\n\n{AUTO_REPLY_TEXT}", cwd=worktree_path, env=self._env_for(entry)
        )

    def _escalate(
        self,
        entry: LocalRepoEntry,
        ticket: Ticket,
        effort: str,
        ticket_path: str,
        ticket_branch: str,
        worktree_path: str,
        push_env: dict[str, str] | None,
        reason: EscalationReason,
    ) -> None:
        """Escalate a ticket the box cannot finish honestly.

        Ticket 30 (§Escalation): writes the escalation brief into the worktree
        ticket file, commits and pushes it, opens the PR as a draft (or
        converts an already-open one), labels it `engine:escalated`, and opens
        one escalation issue (never a second one for the same ticket — ticket 26).
        """
        full_path = pathlib.Path(worktree_path) / ticket_path
        try:
            content = self._read_ticket(full_path)
        except (OSError, FileNotFoundError):
            content = ticket.raw_text or ""

        brief = assemble_escalation_brief(ticket=ticket, branch=ticket_branch)
        updated = apply_escalation_to_ticket_text(content, brief)
        self._write_ticket(full_path, updated)

        if reason == EscalationReason.resumes_exhausted:
            reason_text = "resumes exhausted"
        elif reason == EscalationReason.ci_failed:
            reason_text = "fix attempts exhausted"
        else:
            reason_text = "kept asking"
        commit_message = f"Escalate {ticket.number:02d}: {reason_text}"
        for step, git_args in (
            ("add", ["git", "-C", worktree_path, "add", ticket_path]),
            ("commit", ["git", "-C", worktree_path, "commit", "-m", commit_message]),
        ):
            rc, out = self._git_runner(git_args, worktree_path, None)
            if rc != 0:
                logger.warning(
                    "Escalation %s for ticket %02d failed (rc=%d): %s",
                    step, ticket.number, rc, out.strip()[-2000:],
                )
        self._push_branch(worktree_path, ticket_branch, push_env)

        pr_number: int | None
        existing_pr = self.github_client.find_open_pr(entry.repo, ticket_branch)
        if existing_pr is None:
            title = f"{effort}-{ticket.number:02d}: {ticket.title}"
            body = (
                f"Ticket {ticket.number:02d} worked by the box.\n\n"
                f"Ticket file: {ticket_path}\nBranch: {ticket_branch}"
            )
            try:
                pr_number = self.github_client.create_pull_request(
                    repo=entry.repo,
                    head=ticket_branch,
                    base=self._default_branch(entry),
                    title=title,
                    body=body,
                    draft=True,
                )
            except urllib.error.HTTPError as exc:
                logger.warning(
                    "GitHub refused the escalation PR for %s (HTTP %d); "
                    "opening the escalation issue with a branch link.",
                    ticket_branch, exc.code,
                )
                pr_number = None
        else:
            pr_number = existing_pr
            self.github_client.convert_pr_to_draft(entry.repo, pr_number)

        if pr_number is None:
            link = f"https://github.com/{entry.repo}/tree/{ticket_branch}"
        else:
            self.github_client.add_issue_labels(entry.repo, pr_number, ["engine:escalated"])
            link = f"https://github.com/{entry.repo}/pull/{pr_number}"
        owner = entry.repo.split("/")[0]
        title, body = render_escalation_issue(
            ref=TicketRef(repo=entry.repo, number=ticket.number),
            effort=effort,
            title_slug=ticket.slug,
            link=link,
            reason=reason,
            owner=owner,
        )
        if self.github_client.find_open_issue(entry.repo, "escalation", title) is None:
            self.github_client.create_issue(entry.repo, title, body, ["escalation"])

    def run(self) -> int:
        """Find the first windows ticket across all configured repos and run it.

        Returns 0 on success or when no windows tickets are found; 1 on failure.
        """
        for entry in self.config.repos:
            tickets = self.find_windows_frontier(entry)
            if not tickets:
                continue
            ticket = tickets[0]  # lowest-numbered frontier windows ticket
            logger.info(
                "Running windows ticket %02d: %s from %s",
                ticket.number,
                ticket.title,
                entry.repo,
            )
            success = self.run_one(entry, ticket)
            return 0 if success else 1

        logger.info("No windows tickets on the frontier across all configured repos.")
        return 0

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _worktree_path(self, entry: LocalRepoEntry, ticket: Ticket) -> str:
        effort = ticket.effort or "phase-1"
        slug = f"ticket-{effort}-{ticket.number:02d}-{ticket.slug}"
        if self.config.worktree_base:
            return str(pathlib.Path(self.config.worktree_base) / slug)
        # Default: sibling of repo directory
        repo_parent = pathlib.Path(entry.path).parent
        return str(repo_parent / "worktrees" / slug)

    def _commit_claimed_by(
        self, repo: str, ticket_path: str, claim_branch: str
    ) -> None:
        """Insert Claimed-by: box into the ticket file on the claim branch."""
        try:
            file_data = self.github_client.get_file_contents(
                repo, ticket_path, ref=claim_branch
            )
            content = file_data.get("content", "")
            file_sha = file_data.get("sha") or None
            new_content = insert_claimed_by(content, "box")
            self.github_client.commit_file_change(
                repo=repo,
                path=ticket_path,
                content=new_content,
                message="Claimed-by: box",
                branch=claim_branch,
                sha=file_sha,
            )
        except (OSError, KeyError, RuntimeError) as exc:
            logger.warning("Failed to commit Claimed-by to %s: %s", claim_branch, exc)

    def _create_worktree(
        self,
        repo_path: str,
        worktree_path: str,
        ticket_branch: str,
        claim_branch: str,
    ) -> None:
        """Create a git worktree for ticket_branch.

        An existing worktree folder (one with a `.git` entry) is reused as it
        is: a quota pause or a restart leaves it in place on purpose.
        Otherwise stale worktree registrations are pruned first. If the ticket
        branch already exists on the remote, resume from it using --track (no
        new branch created). Otherwise create the branch from the claim branch
        head with -B, which resets a local branch left over from an earlier
        failed run instead of failing. In both cases, fetch the claim branch
        first so origin/claim/... is available locally.

        Raises WorktreeError, carrying git's output, if the worktree cannot be
        created (ticket 47).
        """
        if (pathlib.Path(worktree_path) / ".git").exists():
            logger.info("Reusing existing worktree %s", worktree_path)
            return

        self._git_runner(
            ["git", "-C", repo_path, "worktree", "prune"],
            repo_path,
            None,
        )

        # Check whether the ticket branch already exists on the remote.
        rc_ls, out_ls = self._git_runner(
            ["git", "-C", repo_path, "ls-remote", "--heads", "origin", ticket_branch],
            repo_path,
            None,
        )
        ticket_branch_exists = bool(out_ls.strip()) and rc_ls == 0

        # Fetch the claim branch so origin/claim/... is available.
        self._git_runner(
            ["git", "-C", repo_path, "fetch", "origin", claim_branch],
            repo_path,
            None,
        )

        if ticket_branch_exists:
            # Resume: add worktree on the existing remote ticket branch with tracking.
            args = [
                "git", "-C", repo_path, "worktree", "add",
                "--track", worktree_path, f"origin/{ticket_branch}",
            ]
        else:
            # Fresh claim: create a new local branch from the claim branch head.
            args = [
                "git", "-C", repo_path, "worktree", "add",
                worktree_path, "-B", ticket_branch, f"origin/{claim_branch}",
            ]

        rc, out = self._git_runner(args, repo_path, None)
        if rc != 0:
            msg = f"git worktree add returned {rc}: {out.strip()}"
            raise WorktreeError(msg)

    def _push_branch(
        self,
        worktree_path: str,
        ticket_branch: str,
        push_env: dict[str, str] | None,
    ) -> None:
        """Push the ticket branch. Failed pushes are logged, never fatal."""
        push_ref = f"HEAD:refs/heads/{ticket_branch}"
        args = ["git", "-C", worktree_path, "push", "origin", push_ref]
        rc, out = self._git_runner(args, worktree_path, push_env)
        if rc != 0:
            logger.warning("Push failed for %s (rc=%d): %s", ticket_branch, rc, out)
            return
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        logger.info("Pushed %s: %s", ticket_branch, lines[-1] if lines else "ok")

    def _cleanup_worktree(
        self, repo_path: str, worktree_path: str, ticket_branch: str
    ) -> None:
        """Remove the worktree only when all commits are pushed and the tree is clean.

        Never passes --force. A worktree with unpushed commits is kept and logged.
        """
        remote_ref = f"origin/{ticket_branch}"

        # Check for unpushed commits.
        rc_rl, out_rl = self._git_runner(
            ["git", "-C", worktree_path, "rev-list", "--count", f"{remote_ref}..HEAD"],
            worktree_path,
            None,
        )
        if rc_rl != 0 or out_rl.strip() != "0":
            logger.warning(
                "Worktree has unpushed commits, keeping: %s", worktree_path
            )
            return

        # Check for uncommitted changes.
        rc_st, out_st = self._git_runner(
            ["git", "-C", worktree_path, "status", "--porcelain"],
            worktree_path,
            None,
        )
        if rc_st != 0 or out_st.strip():
            logger.warning(
                "Worktree has uncommitted changes, keeping: %s", worktree_path
            )
            return

        # Safe to remove.
        rc_rm, out_rm = self._git_runner(
            ["git", "-C", repo_path, "worktree", "remove", worktree_path],
            repo_path,
            None,
        )
        if rc_rm != 0:
            logger.warning("git worktree remove returned %d: %s", rc_rm, out_rm)

    def _force_remove_worktree(self, repo_path: str, worktree_path: str) -> None:
        """Force-remove a worktree the box no longer holds the claim for.

        Ticket 29 (ADR 0006 rule 7): the only place --force is passed. A ticket
        whose claim is gone, or held by Jules, is no longer the box's to save;
        any unpushed local commits here are discarded on purpose.
        """
        rc, out = self._git_runner(
            ["git", "-C", repo_path, "worktree", "remove", "--force", worktree_path],
            repo_path,
            None,
        )
        if rc != 0:
            logger.warning("git worktree remove --force returned %d: %s", rc, out)

    def _claim_owner(self, repo: str, claim_branch: str, ticket_path: str) -> str | None:
        """The `Claimed-by` value on an existing claim branch's ticket file.

        None if the file cannot be read: an unreadable claim is never treated
        as the box's own (ticket 47).
        """
        try:
            file_data = self.github_client.get_file_contents(
                repo, ticket_path, ref=claim_branch
            )
        except (OSError, KeyError, RuntimeError) as exc:
            logger.warning(
                "Failed to read claim branch %s ticket file: %s", claim_branch, exc
            )
            return None
        return TicketParser().parse_text(file_data.get("content", "")).claimed_by

    def _claim_still_mine(self, repo: str, claim_branch: str, ticket_path: str) -> bool:
        """True unless the claim branch is gone, or its ticket file now reads
        `Claimed-by: jules` (ADR 0006 rule 7)."""
        if claim_branch not in self.github_client.list_claim_branches(repo):
            return False
        try:
            file_data = self.github_client.get_file_contents(
                repo, ticket_path, ref=claim_branch
            )
        except (OSError, KeyError, RuntimeError) as exc:
            logger.warning(
                "Failed to read claim branch %s ticket file: %s", claim_branch, exc
            )
            return True
        content = file_data.get("content", "")
        parsed = TicketParser().parse_text(content)
        return parsed.claimed_by != "jules"

    def _push_if_claimed(
        self,
        repo: str,
        claim_branch: str,
        ticket_path: str,
        worktree_path: str,
        ticket_branch: str,
        push_env: dict[str, str] | None,
    ) -> None:
        """Push the ticket branch, unless the box has lost the claim (ticket 29).

        Every push, on the timer and the final push after a run, goes through
        this same `claim_still_mine` check.
        """
        if not self._claim_still_mine(repo, claim_branch, ticket_path):
            logger.info(
                "Lost claim for %s; not pushing %s.", claim_branch, ticket_branch
            )
            return
        self._push_branch(worktree_path, ticket_branch, push_env)

    def _gate_env(self, entry: LocalRepoEntry, cfg: RepoConfig) -> dict[str, str] | None:
        """The repo environment plus the repo's `test_env`, for gate commands."""
        gate_env = self._env_for(entry)
        if cfg.test_env:
            gate_env = {
                **(gate_env if gate_env is not None else os.environ),
                **cfg.test_env,
            }
        return gate_env

    def _publish(
        self,
        entry: LocalRepoEntry,
        ticket: Ticket,
        effort: str,
        ticket_path: str,
        ticket_branch: str,
        claim_branch: str,
        worktree_path: str,
        push_env: dict[str, str] | None,
    ) -> GateResult | None:
        """Push (and open the PR) behind the pre-push gate (ticket 72, ADR 0011 rule 3).

        A ticket that is not `done` on a branch with no open PR cannot be merged,
        so it is pushed ungated and `None` is returned. Otherwise the repo's
        `gate_commands` run in the worktree; red pushes and opens nothing and the
        result is returned, green pushes, opens the PR if none exists, and returns
        the result.
        """
        done = False
        try:
            content = self._read_ticket(pathlib.Path(worktree_path) / ticket_path)
            done = TicketParser().parse_text(content).status == "done"
        except OSError:
            pass
        if not done and self.github_client.find_open_pr(entry.repo, ticket_branch) is None:
            self._push_if_claimed(
                entry.repo, claim_branch, ticket_path, worktree_path, ticket_branch, push_env
            )
            return None

        repo_path = pathlib.Path(entry.path)
        cfg = load_repo_config(repo_path) if repo_path.is_dir() else RepoConfig()
        gate = run_gate(
            list(cfg.gate_commands),
            worktree_path,
            self._gate_env(entry, cfg),
            self._command_runner,
        )
        if gate.passed:
            gate = self._with_local_integrity(
                gate, entry, worktree_path, cfg, push_env
            )
        if not gate.passed:
            logger.warning(
                "pre-push gate red for %s #%02d\n%s",
                entry.repo,
                ticket.number,
                format_gate_report(gate)[-4000:],
            )
            return gate
        self._push_if_claimed(
            entry.repo, claim_branch, ticket_path, worktree_path, ticket_branch, push_env
        )
        self._maybe_open_pull_request(
            entry, ticket, ticket_path, worktree_path, ticket_branch, effort
        )
        return gate

    def _with_local_integrity(
        self,
        gate: GateResult,
        entry: LocalRepoEntry,
        worktree_path: str,
        cfg: RepoConfig,
        push_env: dict[str, str] | None,
    ) -> GateResult:
        """Append the local integrity run to a green gate (ticket 73, ADR 0011 rule 3).

        Exit 0 (a pass or a hold) is a passed entry; any other exit code is a failed
        entry named `integrity gate (local)` carrying the runner's output, so a
        crashed run blocks the push rather than passing unseen.
        """
        branch = cfg.default_branch
        rc, out = self._git_runner(
            ["git", "-C", worktree_path, "fetch", "origin", branch], worktree_path, push_env
        )
        if rc != 0:
            logger.warning("Fetching origin/%s failed (rc=%d): %s", branch, rc, out.strip())
        python = env_paths(self.config.envs_dir, entry.repo, os.name == "nt").python
        engine_src = str(pathlib.Path(ticket_engine.__file__).resolve().parents[1])
        env = {**(self._gate_env(entry, cfg) or os.environ), "PYTHONPATH": engine_src}
        command = [
            str(python),
            "-m",
            "ticket_engine.integrity_runner",
            "--local",
            "--repo-path",
            worktree_path,
            "--base-ref",
            f"origin/{branch}",
        ]
        exit_code, output = self._command_runner(command, worktree_path, env, False)
        output = output or ""
        result = GateCommandResult(
            command="integrity gate (local)",
            exit_code=exit_code,
            passed=exit_code == 0,
            output_tail=output[-4000:],
        )
        return GateResult(
            passed=gate.passed and result.passed, results=(*gate.results, result)
        )

    def _default_branch(self, entry: LocalRepoEntry) -> str:
        """The repo's configured default branch, for a PR's base."""
        repo_path = pathlib.Path(entry.path)
        repo_config = load_repo_config(repo_path) if repo_path.is_dir() else None
        return (repo_config or RepoConfig()).default_branch

    def _maybe_open_pull_request(
        self,
        entry: LocalRepoEntry,
        ticket: Ticket,
        ticket_path: str,
        worktree_path: str,
        ticket_branch: str,
        effort: str,
    ) -> None:
        """Open the ticket's PR once the worktree's ticket file reads Status: done.

        Ticket 29 AC2: base is the repo's default branch, head is the ticket
        branch, title `<effort>-<NN>: <title>`, a fixed body. At most one PR
        per ticket branch (`find_open_pr` first). Skipped entirely if the box
        has lost the claim (ADR 0006 rule 7).
        """
        full_path = pathlib.Path(worktree_path) / ticket_path
        try:
            content = self._read_ticket(full_path)
        except (OSError, FileNotFoundError):
            return
        parsed = TicketParser().parse_text(content)
        if parsed.status != "done":
            return

        claim_branch = f"claim/{effort}/{ticket.number:02d}"
        if not self._claim_still_mine(entry.repo, claim_branch, ticket_path):
            logger.info(
                "Lost claim for %s; not opening a PR for %s.", claim_branch, ticket_branch
            )
            return

        if self.github_client.find_open_pr(entry.repo, ticket_branch) is not None:
            return

        title = f"{effort}-{ticket.number:02d}: {ticket.title}"
        body = (
            f"Ticket {ticket.number:02d} worked by the box.\n\n"
            f"Ticket file: {ticket_path}\nBranch: {ticket_branch}"
        )
        try:
            pr_number = self.github_client.create_pull_request(
                repo=entry.repo,
                head=ticket_branch,
                base=self._default_branch(entry),
                title=title,
                body=body,
            )
            self._record_last_pr(entry.repo, ticket.number, pr_number)
        except urllib.error.HTTPError as exc:
            logger.warning(
                "GitHub refused the PR for %s (HTTP %d); the branch is pushed, the box carries on.",
                ticket_branch, exc.code,
            )
            return

    def _read_progress_note(self, worktree_path: str, ticket_path: str) -> str:
        """Read the progress note from the ticket file inside the worktree."""
        full_path = pathlib.Path(worktree_path) / ticket_path
        try:
            content = self._read_ticket(full_path)
        except (OSError, FileNotFoundError):
            return ""
        return _extract_progress_note(content)

    def _default_ticket_loader(self, entry: LocalRepoEntry) -> list[Ticket]:
        repo_path = pathlib.Path(entry.path)
        return _load_tickets_from_path(repo_path)

    def list_box_claims(
        self, entry: LocalRepoEntry, tickets: list[Ticket]
    ) -> dict[int, str]:
        """Read every `claim/<effort>/<NN>` branch's `Claimed-by` value.

        Ticket 31 (spec §The box loop AC2): the box-worker loop builds
        `BoxWorld.claims` from this, so the claim-branch/`Claimed-by` reading
        `_claim_still_mine` already does is not duplicated a third time
        (AGENTS.md §3 invariant 3). `tickets` are the repo's freshly pulled
        tickets, used to resolve a claimed ticket number to its file path
        (slug included) on the claim branch. A branch whose ticket file
        cannot be read is skipped rather than guessed at.
        """
        claims: dict[int, str] = {}
        for branch in self.github_client.list_claim_branches(entry.repo):
            match = re.match(r"^claim/([\w.-]+)/(\d+)$", branch)
            if not match:
                continue
            effort, number_str = match.groups()
            number = int(number_str)
            ticket = next((t for t in tickets if t.number == number), None)
            ticket_path = (
                _ticket_path_str(ticket, effort)
                if ticket is not None
                else f".scratch/{effort}/issues/{number:02d}.md"
            )
            try:
                file_data = self.github_client.get_file_contents(
                    entry.repo, ticket_path, ref=branch
                )
            except (OSError, KeyError, RuntimeError) as exc:
                logger.warning(
                    "Failed to read claim branch %s ticket file: %s", branch, exc
                )
                continue
            content = file_data.get("content", "")
            parsed = TicketParser().parse_text(content)
            claims[number] = parsed.claimed_by or ""
        return claims


# ------------------------------------------------------------------
# Module-level helpers (pure or thin I/O wrappers)
# ------------------------------------------------------------------

def _load_tickets_from_path(repo_path: pathlib.Path) -> list[Ticket]:
    parser = TicketParser()
    tickets: list[Ticket] = []
    scratch_dir = repo_path / ".scratch"
    if not scratch_dir.is_dir():
        return tickets
    for md_path in repo_path.glob(".scratch/*/issues/*.md"):
        if md_path.name.lower() in ("spec.md", "readme.md"):
            continue
        tickets.append(parser.parse_file(md_path))
    return tickets


def _default_git_runner(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> tuple[int, str]:
    """Run one git command. A timeout is reported as a non-zero result.

    On timeout the child is killed and (124, "... timed out after Ns") is
    returned, so callers handle it through the same non-zero path as any other
    git failure. The message names only the duration: `env` can carry the
    push token, so nothing from it is echoed.
    """
    import os
    import subprocess
    merged_env: dict[str, str] | None = None
    if env:
        merged_env = {**os.environ, **env}
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, cwd=cwd, env=merged_env,
            check=False, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return 124, f"git command timed out after {timeout}s"
    return result.returncode, result.stdout or result.stderr


def _default_read_ticket(path: str | pathlib.Path) -> str:
    return pathlib.Path(path).read_text(encoding="utf-8")


def _default_write_ticket(path: str | pathlib.Path, content: str) -> None:
    pathlib.Path(path).write_text(content, encoding="utf-8")


def _gate_suffix(gate_result: GateResult | None) -> str:
    """The prompt addition for a red pre-push gate; empty when green or not run."""
    if gate_result is None or gate_result.passed:
        return ""
    return "\n\n## PRE-PUSH GATE FAILED \u2014 FIX IT\n" + format_gate_report(gate_result)


def _ticket_path_str(ticket: Ticket, effort: str) -> str:
    """The ticket file's path relative to the repo root, `/`-separated.

    GitHub's contents API, the worker prompt, and `git -C <worktree> add` all
    need this form. `Ticket.path` from `_load_tickets_from_path` is absolute
    (the box's clone), so keep only the part from the last `.scratch` on.
    """
    if ticket.path:
        parts = str(ticket.path).replace("\\", "/").split("/")
        if ".scratch" in parts:
            idx = len(parts) - 1 - parts[::-1].index(".scratch")
            return "/".join(parts[idx:])
    return f".scratch/{effort}/issues/{ticket.number:02d}-{ticket.slug}.md"


def _make_push_env(token: str) -> dict[str, str]:
    """Build per-process git config env vars that authenticate a push.

    The token is encoded in the value, never in the key or any git arg.
    This satisfies ADR 0007 rule 3: the box holds the credential, agy never sees it.
    """
    auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    return {
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {auth}",
    }


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


def _seconds_until_reset(reset_at: datetime.datetime | None) -> float:
    if reset_at is None:
        return _QUOTA_RESET_BUFFER_SECS
    now = datetime.datetime.now(datetime.UTC)
    delta = (reset_at - now).total_seconds() + _QUOTA_RESET_BUFFER_SECS
    return max(delta, 0.0)


def _assemble_checkpoint_prompt(
    skill_text: str,
    repo: str,
    ticket_path: str,
    progress_note: str,
) -> str:
    """Assemble a fresh-session prompt that includes the checkpoint progress note."""
    base_prompt = assemble_prompt(skill_text, repo, ticket_path)
    if not progress_note:
        return base_prompt
    checkpoint_section = (
        "\n\n## RESUMING FROM CHECKPOINT\n"
        "A previous session was interrupted by a quota limit. Continue from:\n\n"
        f"{progress_note}\n"
    )
    return base_prompt + checkpoint_section
