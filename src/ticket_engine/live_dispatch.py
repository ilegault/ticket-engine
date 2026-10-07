"""Live dispatch execution orchestrator.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions and Ticket 06 / Ticket 07 Acceptance Criteria mandate that:
1. Live dispatch orchestrates GitHub and Jules adapters to claim tickets, start Jules sessions,
   escalate failed PRs, release stale claims, and manage circuit-breaker repo pausing.
2. Creates claim branch `claim/<effort>/<NN>` via GitHub API before starting a worker.
3. Skips tickets if the claim branch already exists (claim collision).
4. Creates a Jules session with prompt, sourceContext, title, AUTO_CREATE_PR, and no plan approval.
5. Escalating PRs after 3 failed CI runs: commits `Status: blocked` and escalation brief to PR branch,
   converts PR to draft, and applies `engine:escalated` label.
6. Stale claims sweep runs live on every dispatch run (ADR 0006 rule 5): claims are populated with
   real progress times (from the ticket branch or claim branch head) and claimed_by from the ticket file.
   Box claims stale after `box_stale_claim_hours` (default 8) and Jules claims stale after
   `stale_claim_hours` (default 12 without a live session) are released by deleting the claim branch.
7. Two escalations within 24h sets `TICKET_ENGINE_PAUSED`; clearing it resumes dispatch on next run.
8. Privacy invariant (ADR 0002): Never logs prompts, denylist entries, or secrets.
9. ADR 0004: every run, including a paused one, answers this repo's Jules sessions
   that are waiting on a question (`_handle_waiting_sessions`). The adapter's only
   job is to fetch the activities of waiting sessions and carry out what
   `evaluate_waiting_sessions` decides: send the auto-reply, or commit the escalation
   to the claim branch and then send the stop message. The stop message is sent only
   after the commit lands, because the stop marker is what tells later runs the
   ticket is already escalated.
10. ADR 0006 rule 6 / ticket 25: before evaluating, an unclaimed frontier ticket is
    checked for a box checkpoint — a ticket branch the box pushed and released whose
    head differs from the default branch. When one exists, its progress note is read
    and carried into `WorldSnapshot.checkpoints`, so the pure core can attach a
    `Handoff` to the `StartTicketAction` it hands Jules. This detection is the
    adapter's job, not the core's, because it needs a branch-head SHA comparison and
    a file read that a pure function cannot do.
11. Ticket 26 (§Escalation issues), CONTEXT *Escalation*: every escalation the
    dispatcher carries out (`EscalatePRAction`, `EscalateWaitingSessionAction`)
    also opens one GitHub issue in the ticket's target repo, labelled
    `escalation`, so GitHub Mobile can notify the developer without email. Text
    comes only from `box_status.render_escalation_issue`; at most one issue
    stays open per ticket (`find_open_issue` before `create_issue`). Each
    `dispatch` run also closes any open escalation issue whose ticket is done
    on the default branch or whose claim branch is gone
    (`_close_resolved_escalations`). A failed issue call is logged and recorded
    in `RunFacts.issue_failures`, never raised.
12. Ticket 37: done tickets' claims are released at the top of every run, paused
    or not, before any Jules quota call — the paused path, a failed
    `count_recent_sessions` call, and a live-session match by title alone across
    every repo and effort each used to leave a finished ticket holding a
    concurrency slot. `_release_done_claims` fetches claim branches and Jules
    sessions itself and hands them to the pure `dispatch.release_done_claims`,
    which decides what to release and what to keep, and why.
"""
from __future__ import annotations

import datetime
import logging
import urllib.error
from typing import TYPE_CHECKING, Any

from ticket_engine.box_status import (
    BoxStatus,
    EscalationReason,
    TicketRef,
    parse_box_status,
    parse_escalation_issue_title,
    render_escalation_issue,
)
from ticket_engine.config import RepoConfig
from ticket_engine.dispatch import (
    NO_BOX,
    AnswerSessionAction,
    Claim,
    DispatchCore,
    EscalatedSessionStillWaiting,
    EscalatePRAction,
    EscalateWaitingSessionAction,
    Handoff,
    NoBox,
    OpenPR,
    PauseRepoAction,
    ReleaseClaimAction,
    StartTicketAction,
    WorldSnapshot,
    apply_escalation_to_ticket_text,
    count_repo_starts,
    evaluate_waiting_sessions,
    insert_claimed_by,
    release_done_claims,
    session_resource_name,
    ticket_repo_path,
)
from ticket_engine.parser import TicketParser
from ticket_engine.prompt import assemble_prompt, extract_progress_note, load_ticket_skill
from ticket_engine.repo_list import RepoListError, fetch_repo_list
from ticket_engine.run_report import RunFacts
from ticket_engine.ticket_lint import lint_tickets

if TYPE_CHECKING:
    from ticket_engine.github import GitHubClient
    from ticket_engine.jules import JulesClient
    from ticket_engine.parser import Ticket

logger = logging.getLogger(__name__)

ENGINE_REPO = "ilegault/ticket-engine"



class LiveDispatcher:
    """Orchestrator for live ticket dispatch, escalation, and claim management."""

    def __init__(
        self,
        repo: str,
        github_client: GitHubClient,
        jules_client: JulesClient,
        config: RepoConfig | None = None,
        paused: bool = False,
        skill_text: str | None = None,
    ):
        self.repo = repo
        self.github_client = github_client
        self.jules_client = jules_client
        self.config = config or RepoConfig()
        self.paused = paused
        self._skill_text = skill_text
        self.core = DispatchCore()
        # Error text from the last failed TICKET_ENGINE_PAUSED read; "" when it worked.
        self.pause_check_error = ""
        # What the last dispatch() observed, for the end-of-run report.
        self.last_run = RunFacts(repo=repo)
        self._claim_file_cache: dict[tuple[str, str], dict[str, Any]] = {}

    def _get_claim_file_info(self, path: str, ref: str) -> dict[str, Any]:
        key = (path, ref)
        if key in self._claim_file_cache:
            return self._claim_file_cache[key]
        info = self.github_client.get_file_contents(
            repo=self.repo, path=path, ref=ref
        )
        if isinstance(info, dict):
            self._claim_file_cache[key] = info
        return info

    @property
    def skill_text(self) -> str:
        if self._skill_text is None:
            self._skill_text = load_ticket_skill()
        return self._skill_text

    def check_paused_state(self) -> bool:
        """Check TICKET_ENGINE_PAUSED from repository variables."""
        self.pause_check_error = ""
        try:
            val = self.github_client.get_repo_variable(self.repo, "TICKET_ENGINE_PAUSED")
            if isinstance(val, str):
                self.paused = val.strip().lower() in ("true", "1", "yes")
            elif val is None:
                self.paused = False
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning("Failed to read TICKET_ENGINE_PAUSED variable: %s", exc)
            self.pause_check_error = str(exc)
        return self.paused

    def dispatch_escalations_and_stale_claims(
        self,
        tickets: list[Ticket],
        open_prs: list[OpenPR] | None = None,
        claims: list[Claim | str] | None = None,
        escalations: list[datetime.datetime] | None = None,
        jules_sessions: list[dict[str, Any]] | None = None,
        now: datetime.datetime | None = None,
    ) -> list[object]:
        """Evaluate open PRs and claims, escalating failures and releasing stale claims.

        Jules-only and not wired into live dispatch (ADR 0011 rule 6); the box escalates its own PRs.
        """
        self.check_paused_state()

        snapshot = WorldSnapshot(
            tickets=tickets,
            open_prs=open_prs or [],
            claims=claims or [],
            escalations=escalations or [],
            jules_sessions=jules_sessions or [],
            config=self.config,
            paused=self.paused,
            now=now,
        )
        result = self.core.evaluate(snapshot)
        executed_actions: list[object] = []

        for action in result.actions:
            if isinstance(action, EscalatePRAction):
                ticket = action.ticket
                effort = ticket.effort or "phase-1"
                ticket_path = (
                    str(ticket.path).replace("\\", "/")
                    if ticket.path
                    else f".scratch/{effort}/issues/{ticket.number:02d}-{ticket.slug}.md"
                )

                # 1. Fetch file content from PR branch
                file_sha = None
                current_text = ticket.raw_text
                try:
                    file_info = self.github_client.get_file_contents(
                        repo=self.repo, path=ticket_path, ref=action.branch
                    )
                    if file_info.get("content"):
                        current_text = file_info["content"]
                    file_sha = file_info.get("sha")
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.warning("Failed to fetch ticket content for %s: %s", ticket_path, exc)

                # 2. Update status and append escalation brief
                updated_text = apply_escalation_to_ticket_text(current_text, action.brief)

                # 3. Commit to PR branch
                try:
                    self.github_client.commit_file_change(
                        repo=self.repo,
                        path=ticket_path,
                        content=updated_text,
                        message=f"Escalate {ticket.number:02d}: {action.reason}",
                        branch=action.branch,
                        sha=file_sha,
                    )
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.error("Failed to commit escalation to branch %s: %s", action.branch, exc)

                # 4. Convert PR to draft
                try:
                    self.github_client.convert_pr_to_draft(
                        repo=self.repo,
                        pr_number=action.pr_number,
                    )
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.warning("Failed to convert PR #%s to draft: %s", action.pr_number, exc)

                # 5. Apply engine:escalated label
                try:
                    self.github_client.add_issue_labels(
                        repo=self.repo,
                        issue_number=action.pr_number,
                        labels=["engine:escalated"],
                    )
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.warning("Failed to add label to PR #%s: %s", action.pr_number, exc)

                # 6. Open an escalation issue in the target repo, at most one per
                # ticket (ticket 26, CONTEXT *Escalation*). Text comes only from
                # `render_escalation_issue`; a failed create is logged and
                # recorded, never raised.
                self._open_escalation_issue(
                    ticket=ticket,
                    link=f"https://github.com/{self.repo}/pull/{action.pr_number}",
                    reason=EscalationReason.ci_failed,
                    failures=self.last_run.issue_failures,
                )

                executed_actions.append(action)

            elif isinstance(action, ReleaseClaimAction):
                try:
                    self.github_client.delete_branch(repo=self.repo, branch=action.claim_ref)
                    logger.info("Released stale claim branch %s for %s", action.claim_ref, self.repo)
                    executed_actions.append(action)
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.error("Failed to release claim %s: %s", action.claim_ref, exc)

            elif isinstance(action, PauseRepoAction):
                try:
                    self.github_client.set_repo_variable(
                        repo=self.repo,
                        name="TICKET_ENGINE_PAUSED",
                        value="true",
                    )
                    self.paused = True
                    logger.warning("Circuit breaker tripped: set TICKET_ENGINE_PAUSED for %s", self.repo)
                    executed_actions.append(action)
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.error("Failed to set TICKET_ENGINE_PAUSED variable: %s", exc)

        return executed_actions

    def _open_escalation_issue(
        self,
        ticket: Ticket,
        link: str,
        reason: EscalationReason,
        failures: list[tuple[int, str]],
    ) -> None:
        """Open an escalation issue for `ticket` unless one is already open.

        Ticket 26 (§Escalation issues): text comes only from
        `box_status.render_escalation_issue`, never from free text such as a CI
        log excerpt. At most one issue stays open per ticket, so this always
        checks `find_open_issue` by the exact title first. A failed call is
        logged and recorded in `failures`, never raised: an escalation issue is
        a notification, not something the run should die over.
        """
        effort = ticket.effort or "phase-1"
        try:
            title, body = render_escalation_issue(
                ref=TicketRef(repo=self.repo, number=ticket.number),
                effort=effort,
                title_slug=ticket.slug,
                link=link,
                reason=reason,
                owner=self.repo.split("/")[0],
            )
            existing_issue = self.github_client.find_open_issue(self.repo, "escalation", title)
            if existing_issue is None:
                self.github_client.create_issue(self.repo, title, body, ["escalation"])
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.error("Failed to open escalation issue for ticket %02d: %s", ticket.number, exc)
            failures.append((ticket.number, str(exc)))

    def _close_resolved_escalations(
        self, tickets: list[Ticket], claim_branches: set[str], facts: RunFacts
    ) -> None:
        """Close open `escalation` issues once their ticket is resolved.

        Ticket 26: an issue is resolved once its ticket is `done` on the
        default branch, or its claim branch is gone (merged, superseded, or
        reworked and released). The ticket number and effort are read back only
        from the issue's own title via `parse_escalation_issue_title`, never
        guessed, so a run never closes an issue it cannot identify.
        """
        try:
            open_issues = self.github_client.list_open_issues(self.repo, "escalation")
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning("Failed to list open escalation issues for %s: %s", self.repo, exc)
            return
        if not isinstance(open_issues, list) or not open_issues:
            return

        ticket_by_number = {t.number: t for t in tickets}
        for issue in open_issues:
            if not isinstance(issue, dict):
                continue
            number = issue.get("number")
            parsed = parse_escalation_issue_title(str(issue.get("title") or ""))
            if number is None or parsed is None:
                continue
            effort, ticket_number, _slug = parsed
            ticket = ticket_by_number.get(ticket_number)
            ticket_done = ticket is not None and ticket.is_done()
            claim_gone = f"claim/{effort}/{ticket_number:02d}" not in claim_branches
            if not (ticket_done or claim_gone):
                continue
            try:
                self.github_client.close_issue(self.repo, int(number))
                logger.info(
                    "Closed resolved escalation issue #%s for ticket %02d", number, ticket_number
                )
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                logger.error(
                    "Failed to close escalation issue #%s for ticket %02d: %s",
                    number, ticket_number, exc,
                )
                facts.issue_failures.append((ticket_number, str(exc)))

    def _with_waiting_activities(self, sessions: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Copy of `sessions` where this repo's waiting sessions carry `activities`.

        A failed fetch leaves the key off, which the core reads as "history unknown"
        and never answers.
        """
        out: list[dict[str, Any]] = []
        for sess in sessions:
            if (
                isinstance(sess, dict)
                and str(sess.get("state") or "").strip().upper() == "AWAITING_USER_FEEDBACK"
                and str((sess.get("sourceContext") or {}).get("source", "")).strip("/")
                in {self.repo, f"sources/github/{self.repo}"}
            ):
                name = session_resource_name(sess)
                try:
                    sess = {**sess, "activities": self.jules_client.list_activities(name)}
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.warning("Failed to read activities of waiting session %s: %s", name, exc)
            out.append(sess)
        return out

    def _handle_waiting_sessions(
        self,
        tickets: list[Ticket],
        sessions: list[dict[str, Any]],
        facts: RunFacts,
    ) -> None:
        """Carry out what the core decides for sessions waiting on a question."""
        snapshot = WorldSnapshot(
            tickets=tickets,
            jules_sessions=self._with_waiting_activities(sessions),
            config=self.config,
            repo_name=self.repo,
            paused=self.paused,
        )
        for action in evaluate_waiting_sessions(snapshot):
            if isinstance(action, AnswerSessionAction):
                try:
                    self.jules_client.send_message(action.session_name, action.message)
                    facts.auto_replies.append((action.ticket_number, action.reply_number))
                    logger.info(
                        "Auto-replied to waiting session for ticket %02d (reply %d)",
                        action.ticket_number,
                        action.reply_number,
                    )
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.error("Failed to reply to session for ticket %02d: %s", action.ticket_number, exc)
                    facts.session_failures.append((action.ticket_number, "reply to", str(exc)))

            elif isinstance(action, EscalateWaitingSessionAction):
                num = action.ticket.number
                try:
                    info = self._get_claim_file_info(action.ticket_path, action.claim_ref)
                    current = (info.get("content") or "") if isinstance(info, dict) else ""
                    if not current:
                        msg = f"{action.ticket_path} is empty or missing on {action.claim_ref}"
                        raise ValueError(msg)
                    updated_content = apply_escalation_to_ticket_text(current, action.brief)
                    self.github_client.commit_file_change(
                        repo=self.repo,
                        path=action.ticket_path,
                        content=updated_content,
                        message=f"Escalate {num:02d}: Jules session kept asking for input",
                        branch=action.claim_ref,
                        sha=info.get("sha") if isinstance(info, dict) else None,
                    )
                    self._claim_file_cache[(action.ticket_path, action.claim_ref)] = {
                        "content": updated_content,
                        "sha": info.get("sha") if isinstance(info, dict) else None,
                    }
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.error("Failed to escalate waiting session for ticket %02d: %s", num, exc)
                    facts.session_failures.append((num, "escalate", str(exc)))
                    continue
                facts.session_escalations.append((num, action.claim_ref, action.replies))
                self._open_escalation_issue(
                    ticket=action.ticket,
                    link=f"https://github.com/{self.repo}/tree/{action.claim_ref}",
                    reason=EscalationReason.kept_asking,
                    failures=facts.issue_failures,
                )
                try:
                    self.jules_client.send_message(action.session_name, action.stop_message)
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.error("Failed to send stop message for ticket %02d: %s", num, exc)
                    facts.session_failures.append((num, "stop", str(exc)))

            elif isinstance(action, EscalatedSessionStillWaiting):
                facts.still_escalated.append((action.ticket_number, action.claim_ref))

    def _release_done_claims(self, tickets: list[Ticket], facts: RunFacts) -> set[str]:
        """Release claim branches whose ticket is already done, before any Jules
        quota call and whether or not the repo is paused (ticket 37).

        Calls `github_client.list_claim_branches` and `jules_client.list_sessions`
        itself so this runs at the very top of `dispatch()`, ahead of the paused
        check and the separate quota-counting calls those make later. A failure to
        list either is never raised: a claim branch listing failure returns an
        empty set (nothing to release this run), and a session listing failure is
        passed to the core as `None`, which keeps every done claim rather than
        releasing one blind.
        """
        try:
            claim_refs = self.github_client.list_claim_branches(self.repo)
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning("Failed to list claim branches for %s: %s", self.repo, exc)
            return set()

        sessions: list[dict[str, Any]] | None
        try:
            sessions = self.jules_client.list_sessions()
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning(
                "Failed to list Jules sessions while releasing done claims for %s: %s",
                self.repo,
                exc,
            )
            sessions = None

        released_actions, kept = release_done_claims(tickets, claim_refs, sessions, self.repo)

        released: set[str] = set()
        for action in released_actions:
            try:
                self.github_client.delete_branch(repo=self.repo, branch=action.claim_ref)
                logger.info(
                    "Released done claim branch %s for %s (ticket %02d is done)",
                    action.claim_ref,
                    self.repo,
                    action.ticket_number,
                )
                facts.released_done_claims.append((action.ticket_number, action.claim_ref))
                released.add(action.claim_ref)
            except urllib.error.HTTPError as exc:
                logger.error("Failed to release done claim %s: %s", action.claim_ref, exc)
                facts.kept_done_claims.append(
                    (action.ticket_number, action.claim_ref, f"deleting the branch failed (HTTP {exc.code})")
                )
            except (urllib.error.URLError, ValueError, OSError) as exc:
                logger.error("Failed to release done claim %s: %s", action.claim_ref, exc)
                facts.kept_done_claims.append(
                    (
                        action.ticket_number,
                        action.claim_ref,
                        f"deleting the branch failed ({type(exc).__name__})",
                    )
                )

        facts.kept_done_claims.extend(kept)
        return released

    def _box_covers_repo(self) -> bool:
        """Return True iff engine-repos.toml on the engine repo's default branch lists
        this repo with box = true (ADR 0009 rule 4/5).

        Calls fetch_repo_list(self.github_client, ENGINE_REPO) with no ref.
        Case-insensitive match on repo identifier.
        Errors propagate to the caller.
        """
        entries = fetch_repo_list(self.github_client, ENGINE_REPO)
        repo_lower = self.repo.strip().lower()
        return any(entry.repo.strip().lower() == repo_lower and entry.box for entry in entries)

    def dispatch(self, tickets: list[Ticket]) -> list[Ticket]:
        """Evaluate frontier and launch Jules sessions for eligible tickets.

        Records what it saw in `self.last_run` on every path, including early returns,
        so the end-of-run report can say why nothing started.
        """
        self.check_paused_state()
        facts = RunFacts(
            repo=self.repo,
            paused=self.paused,
            pause_check_error=self.pause_check_error,
            jules_limit=self.config.jules_limit,
            jules_reserve=self.config.jules_reserve,
            daily_cap=self.config.daily_cap,
            concurrency=self.config.concurrency,
            max_auto_replies=self.config.max_auto_replies,
            lint_findings=lint_tickets(tickets),
        )
        self.last_run = facts

        # Release done tickets' claims first, before the paused check and any Jules
        # quota call, so a finished ticket never keeps holding a concurrency slot
        # just because this run was paused or a later quota call failed (ticket 37).
        released_done_refs = self._release_done_claims(tickets, facts)

        if self.paused:
            logger.info("Repository %s is paused (TICKET_ENGINE_PAUSED). Starting nothing.", self.repo)
            # Work in flight still finishes, so a waiting session is still answered.
            try:
                paused_sessions = self.jules_client.list_sessions()
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                logger.warning("Failed to list Jules sessions while paused: %s", exc)
                paused_sessions = []
            self._handle_waiting_sessions(tickets, paused_sessions, facts)
            return []

        # 1. Fetch default branch SHA
        try:
            base_sha = self.github_client.get_default_branch_sha(
                self.repo, self.config.default_branch
            )
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.error("Failed to fetch default branch SHA for %s: %s", self.repo, exc)
            facts.stopped_early = f"could not read the `{self.config.default_branch}` branch ({exc})"
            return []

        # 2. Fetch existing claims from GitHub
        try:
            existing_claims = set(self.github_client.list_claim_branches(self.repo))
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning("Failed to list existing claims for %s: %s", self.repo, exc)
            existing_claims = set()

        # 3. Fetch recent Jules sessions for quota counting
        try:
            recent_jules_count = self.jules_client.count_recent_sessions(hours=24)
            facts.jules_sessions_24h = recent_jules_count
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.error("Failed to query Jules sessions for quota: %s", exc)
            facts.stopped_early = f"could not query Jules sessions for the quota ({exc})"
            return []

        # 4. Count starts in last 24h for this repo from Jules sessions
        repo_starts_24h = 0
        all_sessions = []
        try:
            all_sessions = self.jules_client.list_sessions()
            repo_starts_24h = count_repo_starts(
                all_sessions, repo=self.repo, now=datetime.datetime.now(datetime.UTC), hours=24
            )
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning("Failed to count repo starts in 24h: %s", exc)

        # 4a. Answer or escalate sessions waiting on a question (ADR 0004).
        self._handle_waiting_sessions(tickets, all_sessions, facts)

        # 4b. Subtract this run's already-released done claims, so a claim freed
        # above frees its concurrency slot in the same run (ticket 37).
        existing_claims -= released_done_refs
        ticket_by_number = {t.number: t for t in tickets}
        facts.repo_starts_24h = repo_starts_24h
        facts.claims_in_flight = sorted(existing_claims)

        # 4b-2. Close escalation issues whose ticket is now resolved (ticket 26).
        self._close_resolved_escalations(tickets, existing_claims, facts)

        # 4c. Build Claim objects for remaining claim branches
        built_claims: list[Claim] = []
        for claim_ref in existing_claims:
            claim_parts = claim_ref.strip("/").split("/")
            claim_ticket_num = int(claim_parts[-1]) if claim_parts[-1].isdigit() else 0
            effort = claim_parts[-2] if len(claim_parts) >= 2 else "phase-1"
            claim_ticket = (
                ticket_by_number.get(claim_ticket_num)
                if claim_ticket_num
                else None
            )

            # Read who claimed it from the claim branch ticket file
            claimed_by = ""
            ticket_path_str = (
                ticket_repo_path(claim_ticket)
                if claim_ticket is not None
                else f".scratch/{effort}/issues/{claim_ticket_num:02d}.md"
            )
            try:
                info = self._get_claim_file_info(
                    path=ticket_path_str,
                    ref=claim_ref,
                )
                if isinstance(info, dict):
                    content = info.get("content")
                    if isinstance(content, str) and content:
                        parsed = TicketParser().parse_text(
                            content, filename=f"{claim_ticket_num:02d}.md"
                        )
                        claimed_by = parsed.claimed_by or ""
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                logger.warning(
                    "Failed to read ticket file on claim branch %s for %s: %s",
                    claim_ref,
                    self.repo,
                    exc,
                )

            # Read last progress time from ticket branch, falling back to claim branch
            last_commit_time: datetime.datetime | None = None
            ticket_branch = (
                f"ticket/{effort}-{claim_ticket_num:02d}-{claim_ticket.slug}"
                if claim_ticket and claim_ticket.slug
                else None
            )
            if ticket_branch:
                try:
                    head_time = self.github_client.get_branch_head_time(self.repo, ticket_branch)
                    if isinstance(head_time, datetime.datetime):
                        last_commit_time = head_time
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.warning(
                        "Failed to read head time for ticket branch %s on %s: %s",
                        ticket_branch,
                        self.repo,
                        exc,
                    )

            if last_commit_time is None:
                try:
                    head_time = self.github_client.get_branch_head_time(self.repo, claim_ref)
                    if isinstance(head_time, datetime.datetime):
                        last_commit_time = head_time
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.warning(
                        "Failed to read head time for claim branch %s on %s: %s",
                        claim_ref,
                        self.repo,
                        exc,
                    )

            built_claims.append(
                Claim(
                    ref=claim_ref,
                    ticket_number=claim_ticket_num,
                    effort=effort,
                    last_commit_time=last_commit_time,
                    claimed_by=claimed_by,
                )
            )

        # 4d. Read box status issue if covered by the box in repo list (ADR 0006, ADR 0009)
        box: BoxStatus | None | NoBox = NO_BOX
        box_status_error = ""
        box_covers = False
        try:
            box_covers = self._box_covers_repo()
        except (RepoListError, urllib.error.HTTPError, urllib.error.URLError, OSError) as exc:
            logger.warning("Failed to fetch or parse repo list from %s: %s", ENGINE_REPO, exc)
            box = None
            box_status_error = "repo list unreadable"

        if box_covers:
            try:
                issues = self.github_client.list_issues(
                    repo=ENGINE_REPO,
                    state="open",
                    labels="engine:box-status",
                )
                box_issue = None
                for iss in issues:
                    if isinstance(iss, dict):
                        box_issue = iss
                        break
                if box_issue:
                    body = str(box_issue.get("body") or "")
                    box = parse_box_status(body)
                    if box is None:
                        box_status_error = "unparseable box status body"
                else:
                    box = None
                    box_status_error = "no open issue labelled engine:box-status found"
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                logger.warning("Failed to read box status issue from %s: %s", ENGINE_REPO, exc)
                box = None
                box_status_error = str(exc)

        # 4e. Detect a box checkpoint for an unclaimed frontier ticket, to hand off
        # to Jules (ADR 0006 rule 6, ticket 25). `Runner: windows` tickets are never
        # handed to Jules, so their checkpoints (if any) are not worth reading here.
        #
        # Restored here: this block (and the `checkpoints` variable it defines) was
        # dropped by the merge that landed ticket 25 on master, leaving every call to
        # `dispatch()` raise `NameError: name 'checkpoints' is not defined` below.
        # Ticket 26 needs `dispatch()` working end to end to test escalation-issue
        # closing, so it is restored verbatim from commit 919964b rather than left
        # broken for a separate fix.
        checkpoints: dict[int, Handoff] = {}
        frontier_for_checkpoints = self.core.compute_frontier(WorldSnapshot(tickets=tickets))
        for ticket in frontier_for_checkpoints:
            if ticket.runner == "windows" or not ticket.slug:
                continue
            effort = ticket.effort or "phase-1"
            if f"claim/{effort}/{ticket.number:02d}" in existing_claims:
                continue
            ticket_branch = f"ticket/{effort}-{ticket.number:02d}-{ticket.slug}"
            try:
                branch_head_time = self.github_client.get_branch_head_time(self.repo, ticket_branch)
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                logger.warning(
                    "Failed to check ticket branch %s for a box checkpoint on %s: %s",
                    ticket_branch,
                    self.repo,
                    exc,
                )
                continue
            if branch_head_time is None:
                continue  # No box checkpoint branch pushed for this ticket.

            try:
                branch_sha = self.github_client.get_default_branch_sha(self.repo, ticket_branch)
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                logger.warning(
                    "Failed to read head SHA for ticket branch %s on %s: %s",
                    ticket_branch,
                    self.repo,
                    exc,
                )
                continue
            if branch_sha == base_sha:
                continue  # Ticket branch never diverged from the default branch.

            ticket_path_str = (
                str(ticket.path).replace("\\", "/")
                if ticket.path
                else f".scratch/{effort}/issues/{ticket.number:02d}-{ticket.slug}.md"
            )
            try:
                info = self.github_client.get_file_contents(
                    repo=self.repo,
                    path=ticket_path_str,
                    ref=ticket_branch,
                )
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                logger.warning(
                    "Failed to read ticket file on branch %s for %s: %s",
                    ticket_branch,
                    self.repo,
                    exc,
                )
                continue
            content = (info.get("content") or "") if isinstance(info, dict) else ""
            if not content:
                continue
            checkpoints[ticket.number] = Handoff(
                branch=ticket_branch, note=extract_progress_note(content)
            )

        # 5. Build WorldSnapshot and evaluate pure core
        snapshot = WorldSnapshot(
            tickets=tickets,
            claims=built_claims,
            config=self.config,
            jules_sessions_count_24h=recent_jules_count,
            repo_starts_last_24h=repo_starts_24h,
            jules_sessions=all_sessions,
            paused=self.paused,
            box=box,
            box_status_error=box_status_error,
            checkpoints=checkpoints,
            repo_name=self.repo,
        )
        result = self.core.evaluate(snapshot)

        facts.box_state = result.box_state
        facts.box_checked_in = box.checked_in_at if isinstance(box, BoxStatus) else None
        facts.left_for_box = result.left_for_box
        facts.box_status_error = box_status_error

        # Carry out stale claim releases
        for action in result.actions:
            if isinstance(action, ReleaseClaimAction):
                try:
                    self.github_client.delete_branch(repo=self.repo, branch=action.claim_ref)
                    logger.info("Released stale claim branch %s for %s", action.claim_ref, self.repo)
                    facts.released_stale.append((action.ticket_number, action.reason))
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.error("Failed to release stale claim %s: %s", action.claim_ref, exc)

        started_tickets: list[Ticket] = []
        for action in result.actions:
            if not isinstance(action, StartTicketAction):
                continue

            ticket = action.ticket
            effort = ticket.effort or "phase-1"

            # 6. Create claim branch in GitHub
            claim_created = self.github_client.create_claim_branch(
                repo=self.repo,
                effort=effort,
                ticket_number=ticket.number,
                sha=base_sha,
            )
            if not claim_created:
                facts.claim_collisions.append(ticket.number)
                logger.info(
                    "Claim collision for ticket %02d (%s). Skipped.",
                    ticket.number,
                    ticket.title,
                )
                continue

            # Record Claimed-by: jules on the claim branch (ADR 0006 rule 4)
            ticket_path_str = (
                str(ticket.path).replace("\\", "/")
                if ticket.path
                else f".scratch/{effort}/issues/{ticket.number:02d}-{ticket.slug}.md"
            )
            claim_ref = f"claim/{effort}/{ticket.number:02d}"
            try:
                info = self.github_client.get_file_contents(
                    repo=self.repo,
                    path=ticket_path_str,
                    ref=claim_ref,
                )
                current = (info.get("content") or "") if isinstance(info, dict) else ""
                if not current or not isinstance(current, str):
                    msg = f"{ticket_path_str} is empty or missing on {claim_ref}"
                    raise ValueError(msg)
                self.github_client.commit_file_change(
                    repo=self.repo,
                    path=ticket_path_str,
                    content=insert_claimed_by(current, "jules"),
                    message=f"Claim {ticket.number:02d} for jules",
                    branch=claim_ref,
                    sha=info.get("sha") if isinstance(info, dict) else None,
                )
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                logger.error(
                    "Failed to record Claimed-by: jules on claim branch %s for ticket %02d: %s",
                    claim_ref,
                    ticket.number,
                    exc,
                )

            # 7. Assemble prompt (pure, no logging of content)
            prompt = assemble_prompt(
                self.skill_text, self.repo, ticket_path_str, handoff=action.handoff
            )

            # 8. Create Jules session
            title = f"{effort}-{ticket.number:02d}: {ticket.title}"
            try:
                self.jules_client.create_session(
                    prompt=prompt,
                    repo=self.repo,
                    starting_branch=self.config.default_branch,
                    title=title,
                    automation_mode="AUTO_CREATE_PR",
                    require_plan_approval=False,
                )
                started_tickets.append(ticket)
                facts.started.append(ticket)
                logger.info(
                    "Successfully started Jules session for ticket %02d: %s",
                    ticket.number,
                    ticket.title,
                )
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                facts.start_failures.append(ticket.number)
                logger.error(
                    "Failed to create Jules session for ticket %02d: %s",
                    ticket.number,
                    exc,
                )

        return started_tickets
