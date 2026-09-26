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
"""
from __future__ import annotations

import base64
import datetime
import logging
import pathlib
import threading
import time
from collections.abc import Callable

from ticket_engine.agy import AgyDriver
from ticket_engine.config import load_repo_config
from ticket_engine.dispatch import DispatchCore, WorldSnapshot, insert_claimed_by
from ticket_engine.github import GitHubClient
from ticket_engine.local_config import LocalRepoEntry, LocalWorkerConfig
from ticket_engine.parser import Ticket, TicketParser
from ticket_engine.prompt import (
    assemble_prompt,
    load_ticket_skill,
)
from ticket_engine.prompt import (
    extract_progress_note as _extract_progress_note,
)

logger = logging.getLogger(__name__)

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
    ) -> None:
        self.config = config
        self.github_client = github_client
        self.agy_driver = agy_driver
        self._git_runner: GitRunner = (
            git_runner if git_runner is not None else _default_git_runner
        )
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self._skill_text = skill_text
        self._ticket_loader = (
            ticket_loader if ticket_loader is not None else self._default_ticket_loader
        )
        self._read_ticket = (
            read_ticket_fn if read_ticket_fn is not None else _default_read_ticket
        )

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

        # 2. Commit Claimed-by: box to the ticket file on the claim branch.
        self._commit_claimed_by(entry.repo, ticket_path, claim_branch)

        # 3. Create worktree from the claim branch head (or resume existing ticket branch).
        worktree_path = self._worktree_path(entry, ticket)
        self._create_worktree(entry.path, worktree_path, ticket_branch, claim_branch)

        # 4-6. Start pusher, run agy, push after every outcome.
        push_env = _make_push_env(self.config.github_token) if self.config.github_token else None
        stop_event = threading.Event()

        def do_push() -> None:
            self._push_branch(worktree_path, ticket_branch, push_env)

        interval_s = self.config.checkpoint_push_minutes * 60
        pusher = CheckpointPusher(do_push, interval_s, self._sleep)
        pusher_thread = threading.Thread(
            target=pusher.run_until, args=(stop_event,), daemon=True
        )
        pusher_thread.start()

        try:
            prompt = assemble_prompt(self.skill_text, entry.repo, ticket_path)
            result = self.agy_driver.start(prompt, cwd=worktree_path)
        finally:
            stop_event.set()
            pusher_thread.join(timeout=5)
            do_push()  # Always push after every run, whatever the outcome.

        if result.success:
            self._cleanup_worktree(entry.path, worktree_path, ticket_branch)
            return True

        if result.quota_error:
            return self._handle_quota_error(
                entry=entry,
                ticket=ticket,
                worktree_path=worktree_path,
                ticket_path=ticket_path,
                ticket_branch=ticket_branch,
                push_env=push_env,
                result=result,
            )

        logger.error(
            "agy failed for ticket %02d (outcome: %s)",
            ticket.number,
            result.outcome,
        )
        self._cleanup_worktree(entry.path, worktree_path, ticket_branch)
        return False

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

        If the ticket branch already exists on the remote, resume from it using
        --track (no new branch created). Otherwise create a fresh branch from
        the claim branch head. In both cases, fetch the claim branch first so
        origin/claim/... is available locally.
        """
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
                worktree_path, "-b", ticket_branch, f"origin/{claim_branch}",
            ]

        rc, out = self._git_runner(args, repo_path, None)
        if rc != 0:
            logger.warning("git worktree add returned %d: %s", rc, out)

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

    def _handle_quota_error(
        self,
        entry: LocalRepoEntry,
        ticket: Ticket,
        worktree_path: str,
        ticket_path: str,
        ticket_branch: str,
        push_env: dict[str, str] | None,
        result: object,
    ) -> bool:
        """Keep the claim and worktree, wait for quota reset, then resume."""
        reset_at = getattr(result, "reset_at", None)
        wait_secs = _seconds_until_reset(reset_at)
        logger.info(
            "Quota error for ticket %02d. Waiting %.0f seconds for quota reset.",
            ticket.number,
            wait_secs,
        )
        self._sleep(wait_secs)

        progress_note = self._read_progress_note(worktree_path, ticket_path)
        checkpoint_prompt = _assemble_checkpoint_prompt(
            self.skill_text, entry.repo, ticket_path, progress_note
        )
        fresh_result = self.agy_driver.start(checkpoint_prompt, cwd=worktree_path)

        # Push after the resume run too.
        self._push_branch(worktree_path, ticket_branch, push_env)

        if fresh_result.success:
            self._cleanup_worktree(entry.path, worktree_path, ticket_branch)
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


def _default_git_runner(
    args: list[str],
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    import os
    import subprocess
    merged_env: dict[str, str] | None = None
    if env:
        merged_env = {**os.environ, **env}
    result = subprocess.run(
        args, capture_output=True, text=True, cwd=cwd, env=merged_env, check=False
    )
    return result.returncode, result.stdout or result.stderr


def _default_read_ticket(path: str | pathlib.Path) -> str:
    return pathlib.Path(path).read_text(encoding="utf-8")


def _default_branch_for(entry: LocalRepoEntry) -> str:
    return "master"


def _ticket_path_str(ticket: Ticket, effort: str) -> str:
    if ticket.path:
        return str(ticket.path).replace("\\", "/")
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
