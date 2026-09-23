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
"""
from __future__ import annotations

import datetime
import logging
import urllib.error
from typing import TYPE_CHECKING, Any

from ticket_engine.config import RepoConfig
from ticket_engine.dispatch import (
    Claim,
    DispatchCore,
    EscalatePRAction,
    OpenPR,
    PauseRepoAction,
    ReleaseClaimAction,
    StartTicketAction,
    WorldSnapshot,
    apply_escalation_to_ticket_text,
)
from ticket_engine.prompt import assemble_prompt, load_ticket_skill

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

    @property
    def skill_text(self) -> str:
        if self._skill_text is None:
            self._skill_text = load_ticket_skill()
        return self._skill_text

    def check_paused_state(self) -> bool:
        """Check TICKET_ENGINE_PAUSED from repository variables."""
        try:
            val = self.github_client.get_repo_variable(self.repo, "TICKET_ENGINE_PAUSED")
            if isinstance(val, str):
                self.paused = val.strip().lower() in ("true", "1", "yes")
            elif val is None:
                self.paused = False
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning("Failed to read TICKET_ENGINE_PAUSED variable: %s", exc)
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

    def dispatch(self, tickets: list[Ticket]) -> list[Ticket]:
        """Evaluate frontier and launch Jules sessions for eligible tickets."""
        self.check_paused_state()
        if self.paused:
            logger.info("Repository %s is paused (TICKET_ENGINE_PAUSED). Starting nothing.", self.repo)
            return []

        # 1. Fetch default branch SHA
        try:
            base_sha = self.github_client.get_default_branch_sha(
                self.repo, self.config.default_branch
            )
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.error("Failed to fetch default branch SHA for %s: %s", self.repo, exc)
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
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.error("Failed to query Jules sessions for quota: %s", exc)
            return []

        # 4. Count starts in last 24h for this repo from Jules sessions
        repo_starts_24h = 0
        all_sessions = []
        try:
            all_sessions = self.jules_client.list_sessions()
            for s in all_sessions:
                s_title = s.get("title", "")
                src = s.get("sourceContext", {}).get("source", "")
                if self.repo in src or s_title:
                    repo_starts_24h += 1
        except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
            logger.warning("Failed to count repo starts in 24h: %s", exc)

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
                logger.info(
                    "Successfully started Jules session for ticket %02d: %s",
                    ticket.number,
                    ticket.title,
                )
            except (urllib.error.HTTPError, urllib.error.URLError, ValueError, OSError) as exc:
                logger.error(
                    "Failed to create Jules session for ticket %02d: %s",
                    ticket.number,
                    exc,
                )

        return started_tickets
