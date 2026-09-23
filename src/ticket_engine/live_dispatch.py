"""Live dispatch execution orchestrator.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions and Ticket 06 Acceptance Criteria 1, 2, 4, 6
mandate that the dispatcher:
1. Orchestrates the GitHub and Jules adapters to claim tickets and start Jules sessions.
2. Creates claim branch `claim/<effort>/<NN>` via GitHub API before starting a worker.
3. Skips tickets if the claim branch already exists (claim collision).
4. Creates a Jules session with prompt, sourceContext, title, AUTO_CREATE_PR, and no plan approval.
5. Respects paused flag, daily cap, concurrency, and quota reserve.
6. Privacy invariant (ADR 0002): Never logs prompts or secrets.
"""
from __future__ import annotations

import logging
import urllib.error
from typing import TYPE_CHECKING

from ticket_engine.config import RepoConfig
from ticket_engine.dispatch import DispatchCore, StartTicketAction, WorldSnapshot
from ticket_engine.prompt import assemble_prompt, load_ticket_skill

if TYPE_CHECKING:
    from ticket_engine.github import GitHubClient
    from ticket_engine.jules import JulesClient
    from ticket_engine.parser import Ticket

logger = logging.getLogger(__name__)


class LiveDispatcher:
    """Orchestrator for live ticket dispatch to Jules."""

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

    def dispatch(self, tickets: list[Ticket]) -> list[Ticket]:
        """Evaluate frontier and launch Jules sessions for eligible tickets."""
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
        try:
            all_sessions = self.jules_client.list_sessions()
            for s in all_sessions:
                s_title = s.get("title", "")
                # Count session if it belongs to this repo
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
                # Claim collision: already exists, skip
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
