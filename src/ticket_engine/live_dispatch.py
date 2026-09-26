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
6. Stale claims (no live session, quiet > 12h) are released by deleting the claim branch.
7. Two escalations within 24h sets `TICKET_ENGINE_PAUSED`; clearing it resumes dispatch on next run.
8. Privacy invariant (ADR 0002): Never logs prompts, denylist entries, or secrets.
9. ADR 0004: every run, including a paused one, answers this repo's Jules sessions
   that are waiting on a question (`_handle_waiting_sessions`). The adapter's only
   job is to fetch the activities of waiting sessions and carry out what
   `evaluate_waiting_sessions` decides: send the auto-reply, or commit the escalation
   to the claim branch and then send the stop message. The stop message is sent only
   after the commit lands, because the stop marker is what tells later runs the
   ticket is already escalated.
"""
from __future__ import annotations

import datetime
import logging
import urllib.error
from typing import TYPE_CHECKING, Any

from ticket_engine.config import RepoConfig
from ticket_engine.dispatch import (
    AnswerSessionAction,
    Claim,
    DispatchCore,
    EscalatedSessionStillWaiting,
    EscalatePRAction,
    EscalateWaitingSessionAction,
    OpenPR,
    PauseRepoAction,
    ReleaseClaimAction,
    StartTicketAction,
    WorldSnapshot,
    apply_escalation_to_ticket_text,
    count_repo_starts,
    evaluate_waiting_sessions,
    is_live_session_state,
    session_resource_name,
)
from ticket_engine.prompt import assemble_prompt, load_ticket_skill
from ticket_engine.run_report import RunFacts
from ticket_engine.ticket_lint import lint_tickets

if TYPE_CHECKING:
    from ticket_engine.github import GitHubClient
    from ticket_engine.jules import JulesClient
    from ticket_engine.parser import Ticket

logger = logging.getLogger(__name__)


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
        """Evaluate open PRs and claims, escalating failures and releasing stale claims."""
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
                    info = self.github_client.get_file_contents(
                        repo=self.repo, path=action.ticket_path, ref=action.claim_ref
                    )
                    current = info.get("content") or ""
                    if not current:
                        msg = f"{action.ticket_path} is empty or missing on {action.claim_ref}"
                        raise ValueError(msg)
                    self.github_client.commit_file_change(
                        repo=self.repo,
                        path=action.ticket_path,
                        content=apply_escalation_to_ticket_text(current, action.brief),
                        message=f"Escalate {num:02d}: Jules session kept asking for input",
                        branch=action.claim_ref,
                        sha=info.get("sha"),
                    )
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.error("Failed to escalate waiting session for ticket %02d: %s", num, exc)
                    facts.session_failures.append((num, "escalate", str(exc)))
                    continue
                facts.session_escalations.append((num, action.claim_ref, action.replies))
                try:
                    self.jules_client.send_message(action.session_name, action.stop_message)
                except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                    logger.error("Failed to send stop message for ticket %02d: %s", num, exc)
                    facts.session_failures.append((num, "stop", str(exc)))

            elif isinstance(action, EscalatedSessionStillWaiting):
                facts.still_escalated.append((action.ticket_number, action.claim_ref))

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

        # 4b. Release claim branches for tickets that are already done.
        # A claim branch only ever gets created (step 6 below); nothing else
        # ever deletes it. Once a ticket's PR has merged, its file on the
        # default branch reads Status: done, so if there is also no Jules
        # session still live for it, the claim it made is stale right now,
        # not just after some staleness window. Releasing it immediately
        # (rather than waiting on the separate, currently-unused staleness
        # sweep) is what keeps a concurrency slot from being lost forever
        # every time a ticket finishes.
        ticket_by_number = {t.number: t for t in tickets}
        released_claims: set[str] = set()
        for claim_ref in existing_claims:
            claim_parts = claim_ref.strip("/").split("/")
            claim_ticket_num = int(claim_parts[-1]) if claim_parts[-1].isdigit() else None
            claim_ticket = (
                ticket_by_number.get(claim_ticket_num)
                if claim_ticket_num is not None
                else None
            )
            if claim_ticket is None or not claim_ticket.is_done():
                continue

            has_live_session = any(
                isinstance(s, dict)
                and is_live_session_state(s.get("state"))
                and (
                    f"-{claim_ticket_num:02d}:" in s.get("title", "")
                    or f"-{claim_ticket_num}:" in s.get("title", "")
                )
                for s in all_sessions
            )
            if has_live_session:
                continue

            try:
                self.github_client.delete_branch(repo=self.repo, branch=claim_ref)
                logger.info(
                    "Released completed claim branch %s for %s (ticket %02d is done)",
                    claim_ref,
                    self.repo,
                    claim_ticket_num,
                )
                released_claims.add(claim_ref)
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                logger.error("Failed to release completed claim %s: %s", claim_ref, exc)

        existing_claims -= released_claims
        facts.repo_starts_24h = repo_starts_24h
        facts.claims_in_flight = sorted(existing_claims)

        # 5. Build WorldSnapshot and evaluate pure core
        snapshot = WorldSnapshot(
            tickets=tickets,
            claims=existing_claims,
            config=self.config,
            jules_sessions_count_24h=recent_jules_count,
            repo_starts_last_24h=repo_starts_24h,
            jules_sessions=all_sessions,
            paused=self.paused,
        )
        result = self.core.evaluate(snapshot)

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

            # 7. Assemble prompt (pure, no logging of content)
            ticket_path_str = (
                str(ticket.path).replace("\\", "/")
                if ticket.path
                else f".scratch/{effort}/issues/{ticket.number:02d}-{ticket.slug}.md"
            )
            prompt = assemble_prompt(self.skill_text, self.repo, ticket_path_str)

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
