"""Local worker orchestrator for Windows tickets.

WHY THIS EXISTS
---------------
Ticket 09 and Phase 1 Spec §Local worker (User Stories 44-48) require one
command that finds Runner: windows tickets on the frontier across the
developer's local clones, claims one, works it in a git worktree (never
the developer's own checkout), and drives the Antigravity CLI.

Design choices:
- This module is an orchestrator (adapter layer), not a core. It does I/O.
  The dispatch core and prompt assembly remain pure; this module wires them
  to real filesystem, subprocess, and GitHub operations.
- All I/O seams (subprocess, HTTP, file reading, sleep) are injectable so
  tests can use fakes without spawning real processes or touching the network.
- Worktrees keep agy's commits on a dedicated branch (ticket/<effort>-<NN>-<slug>)
  while leaving the developer's working copy untouched.
- Quota handling: pre-flight check via AgyDriver.read_quota(); on quota error
  during a run, keep the claim, sleep until reset, try agy --continue; if that
  fails, assemble a fresh prompt that includes the last progress note and start
  a new agy session from the checkpoint.
"""
from __future__ import annotations

import datetime
import logging
import pathlib
import re
import time
from collections.abc import Callable

from ticket_engine.agy import AgyDriver
from ticket_engine.config import load_repo_config
from ticket_engine.dispatch import DispatchCore, WorldSnapshot
from ticket_engine.github import GitHubClient
from ticket_engine.local_config import LocalRepoEntry, LocalWorkerConfig
from ticket_engine.parser import Ticket, TicketParser
from ticket_engine.prompt import assemble_prompt, load_ticket_skill

logger = logging.getLogger(__name__)

# Buffer added to the reset time before resuming (seconds).
_QUOTA_RESET_BUFFER_SECS = 60


class LocalWorker:
    """Orchestrates finding, claiming, and working a windows ticket with agy."""

    def __init__(
        self,
        config: LocalWorkerConfig,
        github_client: GitHubClient,
        agy_driver: AgyDriver,
        git_runner: Callable[[list[str], str | None], tuple[int, str]] | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        skill_text: str | None = None,
        ticket_loader: Callable[[LocalRepoEntry], list[Ticket]] | None = None,
        read_ticket_fn: Callable[[str | pathlib.Path], str] | None = None,
    ) -> None:
        self.config = config
        self.github_client = github_client
        self.agy_driver = agy_driver
        self._git_runner = git_runner if git_runner is not None else _default_git_runner
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self._skill_text = skill_text
        self._ticket_loader = ticket_loader if ticket_loader is not None else self._default_ticket_loader
        self._read_ticket = read_ticket_fn if read_ticket_fn is not None else _default_read_ticket

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
        from ticket_engine.config import RepoConfig
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
    ) -> bool:
        """Claim one windows ticket, work it in a worktree with agy.

        Steps:
        1. Pre-flight quota check (refuse if < reserve threshold when endpoint available).
        2. Create claim branch via GitHub API (skip if already claimed).
        3. Fetch default branch SHA and create a git worktree on the ticket branch.
        4. Assemble prompt and drive agy.
        5. On quota error: keep claim, sleep until reset, resume with agy --continue.
           If --continue also fails, start a fresh session from the checkpoint note.
        6. Remove the worktree on completion.

        Returns True on success, False on refusal (quota below reserve, claim
        collision) or unrecoverable failure.
        """
        # 1. Pre-flight quota check
        if not self._quota_ok():
            logger.info(
                "Quota below %.0f%% reserve, refusing to start ticket %02d.",
                self.config.agy_quota_reserve_pct * 100,
                ticket.number,
            )
            return False

        # 2. Claim
        effort = ticket.effort or "phase-1"
        try:
            base_sha = self.github_client.get_default_branch_sha(
                entry.repo, _default_branch_for(entry)
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
        if not claimed:
            logger.info(
                "Ticket %02d already claimed for %s, skipping.",
                ticket.number,
                entry.repo,
            )
            return False

        # 3. Worktree
        ticket_branch = f"ticket/{effort}-{ticket.number:02d}-{ticket.slug}"
        worktree_path = self._worktree_path(entry, ticket)
        self._create_worktree(entry.path, worktree_path, ticket_branch, base_sha)

        try:
            # 4. Drive agy
            ticket_path = _ticket_path_str(ticket, effort)
            prompt = assemble_prompt(self.skill_text, entry.repo, ticket_path)
            result = self.agy_driver.start(prompt, cwd=worktree_path)

            if result.success:
                return True

            if not result.quota_error:
                logger.error(
                    "agy failed for ticket %02d (non-quota): %s",
                    ticket.number,
                    result.raw,
                )
                return False

            # 5. Quota error: wait, then resume
            return self._handle_quota_error(
                entry=entry,
                ticket=ticket,
                worktree_path=worktree_path,
                ticket_path=ticket_path,
                result=result,
            )
        finally:
            # 6. Clean up worktree
            self._remove_worktree(entry.path, worktree_path)

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

    def _quota_ok(self) -> bool:
        """Return True if quota is sufficient (or endpoint is unavailable)."""
        info = self.agy_driver.read_quota(self.config.agy_quota_url)
        if info is None:
            return True  # unavailable → proceed (AC4)
        return info.remaining_pct >= self.config.agy_quota_reserve_pct

    def _worktree_path(self, entry: LocalRepoEntry, ticket: Ticket) -> str:
        effort = ticket.effort or "phase-1"
        slug = f"ticket-{effort}-{ticket.number:02d}-{ticket.slug}"
        if self.config.worktree_base:
            return str(pathlib.Path(self.config.worktree_base) / slug)
        # Default: sibling of repo directory
        repo_parent = pathlib.Path(entry.path).parent
        return str(repo_parent / "worktrees" / slug)

    def _create_worktree(
        self, repo_path: str, worktree_path: str, branch: str, base_sha: str
    ) -> None:
        args = ["git", "-C", repo_path, "worktree", "add", worktree_path, "-b", branch, base_sha]
        rc, out = self._git_runner(args, None)
        if rc != 0:
            logger.warning("git worktree add returned %d: %s", rc, out)

    def _remove_worktree(self, repo_path: str, worktree_path: str) -> None:
        args = ["git", "-C", repo_path, "worktree", "remove", worktree_path, "--force"]
        rc, out = self._git_runner(args, None)
        if rc != 0:
            logger.warning("git worktree remove returned %d: %s", rc, out)

    def _handle_quota_error(
        self,
        entry: LocalRepoEntry,
        ticket: Ticket,
        worktree_path: str,
        ticket_path: str,
        result: object,
    ) -> bool:
        """Keep the claim, wait for quota reset, then resume with --continue.

        Falls back to a fresh agy session assembled from the checkpoint progress
        note if --continue fails (AC6).
        """
        reset_at = getattr(result, "reset_at", None)
        wait_secs = _seconds_until_reset(reset_at)
        logger.info(
            "Quota error for ticket %02d. Waiting %.0f seconds for quota reset.",
            ticket.number,
            wait_secs,
        )
        self._sleep(wait_secs)

        # Try agy --continue
        continue_result = self.agy_driver.continue_session(cwd=worktree_path)
        if continue_result.success:
            return True

        # --continue failed: fresh session from checkpoint (AC6)
        logger.info(
            "agy --continue failed for ticket %02d; starting fresh session from checkpoint.",
            ticket.number,
        )
        progress_note = self._read_progress_note(worktree_path, ticket_path)
        checkpoint_prompt = _assemble_checkpoint_prompt(
            self.skill_text, entry.repo, ticket_path, progress_note
        )
        fresh_result = self.agy_driver.start(checkpoint_prompt, cwd=worktree_path)
        return fresh_result.success

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


def _default_git_runner(args: list[str], cwd: str | None) -> tuple[int, str]:
    import subprocess
    result = subprocess.run(args, capture_output=True, text=True, cwd=cwd, check=False)
    return result.returncode, result.stdout or result.stderr


def _default_read_ticket(path: str | pathlib.Path) -> str:
    return pathlib.Path(path).read_text(encoding="utf-8")


def _default_branch_for(entry: LocalRepoEntry) -> str:
    return "master"


def _ticket_path_str(ticket: Ticket, effort: str) -> str:
    if ticket.path:
        return str(ticket.path).replace("\\", "/")
    return f".scratch/{effort}/issues/{ticket.number:02d}-{ticket.slug}.md"


def _seconds_until_reset(reset_at: datetime.datetime | None) -> float:
    if reset_at is None:
        return _QUOTA_RESET_BUFFER_SECS
    now = datetime.datetime.now(datetime.UTC)
    delta = (reset_at - now).total_seconds() + _QUOTA_RESET_BUFFER_SECS
    return max(delta, 0.0)


def _extract_progress_note(ticket_text: str) -> str:
    """Extract the most recent progress note from the ## Comments section."""
    match = re.search(r"^##\s+Comments\s*\n(.*?)(?=^##|\Z)", ticket_text, re.MULTILINE | re.DOTALL)
    if not match:
        return ""
    return match.group(1).strip()


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
