"""Tests for the local worker: agy driver, local config, and worker orchestration.

WHY THIS EXISTS
---------------
Ticket 09 Acceptance Criteria 1-8 mandate that:
1. A local config lists developer's clones; the command finds windows frontier tickets.
2. The worker claims one ticket via claim/<effort>/<NN> and works in a separate worktree.
3. It drives agy -p <prompt> --output-format json.
4. It reads quota before starting and refuses below 20% (proceeding if unavailable).
5. The assembled prompt contains checkpoint instructions.
6. On quota error: keeps claim, waits, resumes with agy --continue; falls back to fresh
   session if --continue fails.
7. Tests use fake agy: start, quota refusal, quota stop then resume, resume fallback.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from ticket_engine.agy import AgyDriver, AgyResult
from ticket_engine.dispatch import AUTO_REPLY_TEXT
from ticket_engine.local_config import LocalRepoEntry, LocalWorkerConfig, load_local_config
from ticket_engine.local_worker import (
    LocalWorker,
    _load_tickets_from_path,
    _ticket_path_str,
)
from ticket_engine.parser import Ticket
from ticket_engine.sonnet import SonnetResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_ticket(
    number: int,
    status: str = "ready-for-agent",
    runner: str = "windows",
    blocked_by: list[int] | None = None,
    effort: str = "phase-1",
) -> Ticket:
    return Ticket(
        number=number,
        title=f"Ticket {number}",
        slug=f"ticket-{number}",
        status=status,
        runner=runner,
        blocked_by=blocked_by or [],
        effort=effort,
    )


def make_repo_entry(path: str = "/fake/repo", repo: str = "owner/repo") -> LocalRepoEntry:
    return LocalRepoEntry(path=path, repo=repo)


def make_config(**kwargs) -> LocalWorkerConfig:
    return LocalWorkerConfig(
        repos=[make_repo_entry()],
        **kwargs,
    )


class _AllClaims(list):
    """A list-like stand-in that reports every claim branch as present.

    Most tests build the fake GitHub client before `run_one` knows the ticket
    or effort it will claim, so `list_claim_branches` must answer "yes, the box
    still holds this claim" for whatever branch name it is asked about, unless
    a test overrides `list_claim_branches.return_value` with a real list (or an
    empty one) to simulate a lost claim (ticket 29).
    """

    def __contains__(self, item: object) -> bool:
        return True


def make_fake_github(claim_result: bool = True) -> object:
    from unittest.mock import MagicMock
    mock = MagicMock()
    mock.get_default_branch_sha.return_value = "abc123"
    mock.create_claim_branch.return_value = claim_result
    mock.list_claim_branches.return_value = _AllClaims()
    mock.get_file_contents.return_value = {
        "content": "# Ticket\n**Status:** ready-for-agent\n",
        "sha": "def456",
    }
    mock.commit_file_change.return_value = {}
    mock.find_open_pr.return_value = None
    mock.create_pull_request.return_value = 1
    return mock



# ---------------------------------------------------------------------------
# AC1: Local config and windows frontier
# ---------------------------------------------------------------------------

def test_load_local_config_from_toml(tmp_path):
    """load_local_config reads repos and agy settings from a TOML file."""
    config_file = tmp_path / "local.toml"
    config_file.write_text(
        '[agy]\nprint_timeout = "3600"\n\n'
        '[[repos]]\npath = "/code/myrepo"\nrepo = "owner/myrepo"\n',
        encoding="utf-8",
    )
    config = load_local_config(config_file)
    assert len(config.repos) == 1
    assert config.repos[0].path == "/code/myrepo"
    assert config.repos[0].repo == "owner/myrepo"
    assert config.print_timeout == "3600"


def test_load_local_config_missing_file_returns_defaults(tmp_path):
    """load_local_config returns defaults when file does not exist."""
    config = load_local_config(tmp_path / "nonexistent.toml")
    assert config.repos == []
    assert config.print_timeout == "2h"


def test_load_local_config_sonnet_keys_default(tmp_path):
    """With neither sonnet key set, load_local_config returns the stated defaults."""
    config_file = tmp_path / "local.toml"
    config_file.write_text(
        '[[repos]]\npath = "/code/myrepo"\nrepo = "owner/myrepo"\n',
        encoding="utf-8",
    )
    config = load_local_config(config_file)
    assert config.sonnet_enabled is False
    assert config.sonnet_timeout_seconds == 7200


def test_load_local_config_sonnet_keys_round_trip(tmp_path):
    """sonnet_enabled and sonnet_timeout_seconds are flat top-level TOML keys."""
    config_file = tmp_path / "local.toml"
    config_file.write_text(
        "sonnet_enabled = true\nsonnet_timeout_seconds = 3600\n",
        encoding="utf-8",
    )
    config = load_local_config(config_file)
    assert config.sonnet_enabled is True
    assert config.sonnet_timeout_seconds == 3600


def test_windows_frontier_returns_only_windows_tickets():
    """find_windows_frontier returns only runner=windows tickets on the frontier."""
    tickets = [
        make_ticket(1, runner="windows"),
        make_ticket(2, runner="any"),
        make_ticket(3, runner="windows"),
    ]
    worker = _make_worker_with_tickets(tickets)
    entry = make_repo_entry()
    frontier = worker.find_windows_frontier(entry)
    numbers = [t.number for t in frontier]
    assert 1 in numbers
    assert 3 in numbers
    assert 2 not in numbers  # runner=any is not a windows ticket


def test_windows_frontier_excludes_blocked_tickets():
    """find_windows_frontier excludes windows tickets whose blocker is not done."""
    tickets = [
        make_ticket(1, runner="windows"),
        make_ticket(2, runner="windows", blocked_by=[1]),  # blocker 1 not done
    ]
    worker = _make_worker_with_tickets(tickets)
    entry = make_repo_entry()
    frontier = worker.find_windows_frontier(entry)
    assert len(frontier) == 1
    assert frontier[0].number == 1


def test_windows_frontier_excludes_non_ready_tickets():
    """find_windows_frontier excludes tickets not in ready-for-agent status."""
    tickets = [
        make_ticket(1, runner="windows", status="in-progress"),
        make_ticket(2, runner="windows", status="ready-for-agent"),
    ]
    worker = _make_worker_with_tickets(tickets)
    entry = make_repo_entry()
    frontier = worker.find_windows_frontier(entry)
    assert len(frontier) == 1
    assert frontier[0].number == 2


# ---------------------------------------------------------------------------
# AC2: Claim branch and worktree
# ---------------------------------------------------------------------------

def test_run_one_creates_claim_branch_before_agy_starts():
    """run_one creates claim/<effort>/<NN> via GitHub API before invoking agy."""
    calls = []

    def run_fn(args, cwd=None):
        calls.append(args)
        return 0, json.dumps({"status": "success"})

    mock_github = make_fake_github(claim_result=True)
    worktree_calls = []
    driver = AgyDriver(run_fn=run_fn)
    ticket = make_ticket(9, effort="phase-1")
    entry = make_repo_entry()
    config = make_config()

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner(worktree_calls),
    )

    worker.run_one(entry, ticket)

    # Claim was created before agy ran
    mock_github.create_claim_branch.assert_called_once()
    call_kwargs = mock_github.create_claim_branch.call_args.kwargs
    assert call_kwargs["effort"] == "phase-1"
    assert call_kwargs["ticket_number"] == 9
    # And agy was then called
    assert any("agy" in str(a) for a in calls), "agy should have been invoked"


def test_run_one_skips_when_claim_already_exists():
    """run_one returns without starting agy when claim branch already exists (collision)."""
    agy_calls = []

    def run_fn(args, cwd=None):
        agy_calls.append(args)
        return 0, json.dumps({"status": "success"})

    mock_github = make_fake_github(claim_result=False)  # claim fails -> already claimed
    driver = AgyDriver(run_fn=run_fn)
    ticket = make_ticket(9)
    entry = make_repo_entry()
    config = make_config()

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
    )

    success = worker.run_one(entry, ticket)

    assert not success, "Claimed collision should return False"
    assert agy_calls == [], "agy must not be invoked when claim already exists"


def test_run_one_creates_worktree_separate_from_repo_path():
    """run_one creates a git worktree at a path distinct from the repo checkout path."""
    worktree_calls = []

    def run_fn(args, cwd=None):
        return 0, json.dumps({"status": "success"})

    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=run_fn)
    ticket = make_ticket(9)
    entry = make_repo_entry(path="/fake/myrepo")
    config = make_config(worktree_base="/fake/worktrees")

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner(worktree_calls),
    )
    worker.run_one(entry, ticket)

    # A worktree add command should have been issued
    worktree_add_calls = [c for c in worktree_calls if "worktree" in c and "add" in c]
    assert worktree_add_calls, "git worktree add must be called"
    # The worktree path must differ from the repo path
    for call in worktree_add_calls:
        worktree_path = _extract_worktree_path(call)
        assert worktree_path != "/fake/myrepo", "Worktree must not be the repo itself"
        assert "/fake/myrepo" not in worktree_path or worktree_path != "/fake/myrepo"


# ---------------------------------------------------------------------------
# AC3: agy invocation
# ---------------------------------------------------------------------------

def test_run_one_invokes_agy_with_assembled_prompt_and_json_format():
    """run_one drives agy -p <prompt> --output-format json."""
    agy_args_seen = []

    def run_fn(args, cwd=None):
        agy_args_seen.append(args)
        return 0, json.dumps({"status": "success"})

    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=run_fn)
    ticket = make_ticket(9, effort="phase-1")
    entry = make_repo_entry(repo="owner/myrepo")
    config = make_config()

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
        skill_text="# Skill\nCheckpoint at each AC boundary.",
    )
    worker.run_one(entry, ticket)

    assert agy_args_seen, "agy must be invoked"
    call_args = agy_args_seen[0]
    assert call_args[0] == "agy"
    assert "-p" in call_args
    assert "--output-format" in call_args
    idx = call_args.index("--output-format")
    assert call_args[idx + 1] == "json"
    assert "--dangerously-skip-permissions" in call_args
    assert "--print-timeout" in call_args
    # Prompt must be present right after -p
    p_idx = call_args.index("-p")
    prompt_text = call_args[p_idx + 1]
    assert "Ticket 9" in prompt_text or "ticket" in prompt_text.lower()


# ---------------------------------------------------------------------------
# AC5: Skill checkpoint instructions
# ---------------------------------------------------------------------------

def test_assembled_prompt_contains_checkpoint_instruction():
    """The prompt assembled for agy contains checkpoint instructions."""
    from ticket_engine.prompt import assemble_prompt, load_ticket_skill

    skill_text = load_ticket_skill()
    prompt = assemble_prompt(skill_text, "owner/repo", ".scratch/phase-1/issues/09-test.md")

    # Skill section 3 contains "checkpoint" instructions for local workers
    assert "checkpoint" in prompt.lower() or "commit and push" in prompt.lower(), (
        "Assembled prompt must contain checkpoint instructions for local workers"
    )


# ---------------------------------------------------------------------------
# AC6: Quota error handling (resumes with fresh session from checkpoint)
# ---------------------------------------------------------------------------

def test_resume_fallback_to_fresh_session_when_continue_fails():
    """On quota error, keeps claim, sleeps, and resumes with a fresh session from checkpoint."""
    reset_time = "2026-09-23T06:00:00Z"
    run_calls = []

    def tracking_run_fn(args, cwd=None):
        run_calls.append(list(args))
        p_calls_so_far = sum(1 for c in run_calls if "-p" in c)
        if p_calls_so_far >= 2:
            return 0, json.dumps({"status": "SUCCESS"})
        return 1, json.dumps({"status": "ERROR", "message": "quota exceeded", "reset_at": reset_time})

    ticket = make_ticket(9, effort="phase-1")
    ticket_content = (
        "# 09: Test\n**Status:** in-progress\n\n## Comments\n\n"
        "Progress (2026-09-23 05:00): criteria 1-2 done.\nNext: criterion 3.\n"
    )

    slept = []
    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=tracking_run_fn)
    config = make_config()

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
        sleep_fn=lambda secs: slept.append(secs),
        read_ticket_fn=lambda path: ticket_content,
    )
    success = worker.run_one(make_repo_entry(), ticket)

    assert success, "Should succeed after quota-error + fresh session"
    mock_github.delete_branch.assert_not_called()
    assert slept, "Must sleep waiting for quota reset"
    p_calls = [args for args in run_calls if "-p" in args]
    assert len(p_calls) == 2, "A fresh agy session must be started from checkpoint"
    assert not any("--continue" in args for args in run_calls), "No --continue should be passed"
    fresh_p_idx = p_calls[1].index("-p")
    fresh_prompt = p_calls[1][fresh_p_idx + 1]
    assert "Progress" in fresh_prompt or "criteria 1-2" in fresh_prompt
    assert "## RESUMING FROM CHECKPOINT" in fresh_prompt


# ---------------------------------------------------------------------------
# AC8: Fake agy test scenarios (explicit scenario coverage)
# ---------------------------------------------------------------------------

def test_fake_agy_start_success_scenario():
    """Fake agy: normal start returns success."""
    def run_fn(args, cwd=None):
        assert args[0] == "agy"
        assert "-p" in args
        assert "--output-format" in args
        return 0, json.dumps({"status": "success"})

    driver = AgyDriver(run_fn=run_fn)
    result = driver.start("do the thing")
    assert result.success
    assert not result.quota_error


def test_fake_agy_resume_fallback_to_fresh_session_scenario():
    """Fake agy: quota stop, fresh session started from checkpoint."""
    run_calls = []
    p_call_count = [0]

    def run_fn(args, cwd=None):
        run_calls.append(list(args))
        p_call_count[0] += 1
        if p_call_count[0] == 1:
            return 1, json.dumps({"status": "ERROR", "message": "quota exhausted", "reset_at": "2026-09-23T06:00:00Z"})
        # Second start (fresh session) succeeds
        return 0, json.dumps({"status": "SUCCESS"})

    ticket_content = (
        "# 09: Test\n**Status:** in-progress\n\n## Comments\n\n"
        "Progress (2026-09-23 05:00): criteria 1-2 done.\nNext: criterion 3.\n"
    )

    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=run_fn)
    config = make_config()

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
        sleep_fn=lambda secs: None,
        read_ticket_fn=lambda path: ticket_content,
    )
    success = worker.run_one(make_repo_entry(), make_ticket(9))

    assert success
    p_calls = [a for a in run_calls if "-p" in a]
    assert len(p_calls) == 2, "Exactly two -p calls: original + fresh session"
    assert not any("--continue" in args for args in run_calls)
    fresh_p_idx = p_calls[1].index("-p")
    fresh_prompt = p_calls[1][fresh_p_idx + 1]
    # Fresh prompt must include the progress note context
    assert "Progress" in fresh_prompt or "criteria 1-2" in fresh_prompt


# ---------------------------------------------------------------------------
# AgyDriver unit tests
# ---------------------------------------------------------------------------

def test_agy_driver_start_parses_success():
    """AgyDriver.start returns AgyResult(success=True, outcome='success') on returncode=0 success JSON."""
    driver = AgyDriver(run_fn=lambda args, cwd=None: (0, json.dumps({"status": "SUCCESS"})))
    result = driver.start("my prompt")
    assert result.success
    assert result.outcome == "success"
    assert not result.quota_error


def test_agy_driver_start_parses_quota_error():
    """AgyDriver.start returns AgyResult(outcome='quota', quota_error=True) on quota error JSON."""
    driver = AgyDriver(
        run_fn=lambda args, cwd=None: (
            1,
            json.dumps({"status": "ERROR", "message": "rate limit exhausted", "reset_at": "2026-09-23T06:00:00Z"}),
        )
    )
    result = driver.start("my prompt")
    assert not result.success
    assert result.outcome == "quota"
    assert result.quota_error
    assert result.reset_at is not None


# ---------------------------------------------------------------------------
# LocalWorker.run() integration: finds first windows ticket across repos
# ---------------------------------------------------------------------------

def test_run_finds_first_windows_ticket_and_works_it():
    """LocalWorker.run() picks the first windows ticket across all repos and runs it."""
    worked = []

    def run_fn(args, cwd=None):
        worked.append(args)
        return 0, json.dumps({"status": "SUCCESS"})

    tickets = [make_ticket(1, runner="windows")]
    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=run_fn)
    config = make_config()

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
        ticket_loader=lambda entry: tickets,
    )
    exit_code = worker.run()
    assert exit_code == 0
    assert worked, "agy must have been invoked"


def test_run_returns_zero_with_no_windows_tickets():
    """LocalWorker.run() exits 0 with message when no windows tickets are found."""
    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}'))
    config = make_config()

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
        ticket_loader=lambda entry: [make_ticket(1, runner="any")],  # no windows tickets
    )
    exit_code = worker.run()
    assert exit_code == 0


# ---------------------------------------------------------------------------
# CLI integration
# ---------------------------------------------------------------------------

def test_cli_local_worker_main_help():
    """The local_worker_main entry point exists and shows help without error."""
    from ticket_engine.cli import local_worker_main

    with pytest.raises(SystemExit) as exc_info:
        local_worker_main(["--help"])
    assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _make_worker_with_tickets(tickets: list[Ticket]) -> LocalWorker:
    """Build a LocalWorker whose ticket_loader returns the given tickets."""
    from unittest.mock import MagicMock
    mock_github = MagicMock()
    driver = AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}'))
    config = make_config()
    return LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
        ticket_loader=lambda entry: tickets,
    )


def _make_git_runner(call_log: list, scripted: dict | None = None):
    """Create a fake git runner that records calls (3-arg signature: args, cwd, env)."""
    # By default rev-list returns "0" (0 unpushed commits) so worktree removal proceeds.
    _scripted = {"rev-list": (0, "0"), **(scripted or {})}

    def git_runner(
        args: list[str], cwd: str | None = None, env: dict | None = None
    ) -> tuple[int, str]:
        call_log.append(list(args))
        for key, result in _scripted.items():
            if key in args:
                return result
        return 0, ""

    return git_runner


def _extract_worktree_path(args: list) -> str:
    """Extract the worktree path from a git worktree add command args list."""
    # Format: ["git", ..., "worktree", "add", <path>, ...]
    try:
        idx = args.index("add")
        return str(args[idx + 1])
    except (ValueError, IndexError):
        return ""


# ---------------------------------------------------------------------------
# AC1: Claim with Claimed-by, then branch from claim head
# ---------------------------------------------------------------------------

def test_run_one_commits_claimed_by_to_claim_branch():
    """run_one commits the ticket file with Claimed-by: box to the claim branch."""
    ticket = make_ticket(9, effort="box-primary-worker")
    entry = make_repo_entry()
    gh = make_fake_github()

    worker = LocalWorker(
        config=make_config(),
        github_client=gh,
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
        git_runner=_make_git_runner([]),
    )
    worker.run_one(entry, ticket)

    # get_file_contents must be called on the claim branch. (Ticket 29 also
    # reads it via claim_still_mine before every push, so it is no longer
    # called exactly once, but every call must be against the claim branch.)
    assert gh.get_file_contents.call_args_list, "get_file_contents must be called"
    for call in gh.get_file_contents.call_args_list:
        _, kwargs = call
        assert kwargs.get("ref") == "claim/box-primary-worker/09" or \
            "claim/box-primary-worker/09" in call[0]

    # commit_file_change must be called with Claimed-by: box in the content
    gh.commit_file_change.assert_called_once()
    committed_content = gh.commit_file_change.call_args[1].get("content") or \
        gh.commit_file_change.call_args[0][2]
    assert "box" in committed_content.lower()
    assert "Claimed-by" in committed_content


def test_run_one_git_arg_order_for_fresh_claim():
    """Git args for a fresh claim are in order: ls-remote, fetch, worktree add."""
    git_calls = []
    ticket = make_ticket(9, effort="box-primary-worker")
    entry = make_repo_entry()

    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
        git_runner=_make_git_runner(git_calls),
    )
    worker.run_one(entry, ticket)

    ticket_branch = "ticket/box-primary-worker-09-ticket-9"

    ls_remote_calls = [c for c in git_calls if "ls-remote" in c]
    assert ls_remote_calls, "git ls-remote must be called"
    assert ticket_branch in ls_remote_calls[0]

    fetch_calls = [c for c in git_calls if "fetch" in c and "ls-remote" not in c]
    assert fetch_calls, "git fetch must be called"
    assert "claim/box-primary-worker/09" in fetch_calls[0]

    wt_add_calls = [c for c in git_calls if "worktree" in c and "add" in c]
    assert wt_add_calls, "git worktree add must be called"
    wt_args = wt_add_calls[0]
    # -B, not -b: a stale local branch left by an earlier failed run is reset
    # to the claim head instead of making `worktree add` fail (ticket 47).
    assert "-B" in wt_args
    assert "-b" not in wt_args
    assert ticket_branch in wt_args
    assert "origin/claim/box-primary-worker/09" in wt_args

    # order: ls-remote first, then fetch, then worktree add
    ls_idx = next(i for i, c in enumerate(git_calls) if "ls-remote" in c)
    fetch_idx = next(i for i, c in enumerate(git_calls) if "fetch" in c and "ls-remote" not in c)
    wt_idx = next(i for i, c in enumerate(git_calls) if "worktree" in c and "add" in c)
    assert ls_idx < fetch_idx < wt_idx


def test_run_one_resumes_from_existing_ticket_branch():
    """When the ticket branch exists on remote, worktree uses --track (no -b)."""
    git_calls = []
    ticket_branch = "ticket/phase-1-09-ticket-9"
    # ls-remote returns a non-empty line (ticket branch exists)
    runner = _make_git_runner(
        git_calls,
        {"ls-remote": (0, f"abc123\trefs/heads/{ticket_branch}\n")},
    )

    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
        git_runner=runner,
    )
    worker.run_one(make_repo_entry(), make_ticket(9))

    wt_add = [c for c in git_calls if "worktree" in c and "add" in c]
    assert wt_add, "git worktree add must be called"
    wt_args = wt_add[0]
    assert "--track" in wt_args
    assert f"origin/{ticket_branch}" in wt_args
    assert "-b" not in wt_args


# ---------------------------------------------------------------------------
# AC2: Push after every run, and on a timer
# ---------------------------------------------------------------------------

def test_checkpoint_pusher_pushes_on_timer():
    """CheckpointPusher pushes at 0, 20 min, and 40 min, then stops when event is set."""
    import threading

    from ticket_engine.local_worker import CheckpointPusher

    push_calls = []
    slept = []
    stop_event = threading.Event()

    def sleep_fn(secs):
        slept.append(secs)
        if len(slept) >= 2:
            stop_event.set()

    pusher = CheckpointPusher(
        push_fn=lambda: push_calls.append(True),
        interval_s=20 * 60,
        sleep_fn=sleep_fn,
    )
    pusher.run_until(stop_event)

    # Pushes at t=0, t=20min, t=40min
    assert len(push_calls) == 3, f"Expected 3 pushes, got {len(push_calls)}"
    assert all(s == 20 * 60 for s in slept)


def test_push_after_agy_success():
    """After agy returns success, the orchestrator pushes the ticket branch."""
    git_calls = []
    ticket = make_ticket(9, effort="phase-1")

    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
        git_runner=_make_git_runner(git_calls),
    )
    worker.run_one(make_repo_entry(), ticket)

    push_calls = [c for c in git_calls if "push" in c]
    assert push_calls, "At least one push must happen after agy returns"
    # Push refs must include the ticket branch
    ticket_branch = "ticket/phase-1-09-ticket-9"
    assert any(ticket_branch in " ".join(c) for c in push_calls)


def test_push_after_agy_quota_failure():
    """After agy returns a quota failure, the orchestrator still pushes."""
    git_calls = []
    ticket = make_ticket(9)

    call_count = [0]

    def run_fn(args, cwd=None):
        call_count[0] += 1
        if call_count[0] == 1:
            return 1, '{"status": "ERROR", "message": "quota exhausted"}'
        return 0, '{"status": "SUCCESS"}'

    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=_make_git_runner(git_calls),
        sleep_fn=lambda s: None,
        read_ticket_fn=lambda p: "# T\n**Status:** in-progress\n\n## Comments\n",
    )
    worker.run_one(make_repo_entry(), ticket)

    push_calls = [c for c in git_calls if "push" in c]
    assert push_calls, "Push must happen even after quota failure"


def test_failed_push_is_logged_not_fatal(caplog):
    """A failed push is logged but does not stop the run."""
    import logging

    git_calls = []
    ticket = make_ticket(9)
    # push always fails
    runner = _make_git_runner(git_calls, {"push": (1, "permission denied")})

    with caplog.at_level(logging.WARNING, logger="ticket_engine.local_worker"):
        worker = LocalWorker(
            config=make_config(),
            github_client=make_fake_github(),
            agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
            git_runner=runner,
        )
        result = worker.run_one(make_repo_entry(), ticket)

    # Run must complete (not raise)
    assert result is True or result is False  # any bool, just not an exception
    assert any("push" in r.getMessage().lower() for r in caplog.records), \
        "Failed push must be logged"


# ---------------------------------------------------------------------------
# AC3: Token never on disk or in logs
# ---------------------------------------------------------------------------

def test_token_not_in_git_args_or_logs(caplog):
    """The GitHub token and its base64 encoding never appear in git arg lists or logs."""
    import base64
    import logging

    token = "tok-123"
    token_b64 = base64.b64encode(f"x-access-token:{token}".encode()).decode()

    git_calls = []
    git_envs = []

    def recording_runner(args, cwd=None, env=None):
        git_calls.append(list(args))
        git_envs.append(env)
        if "rev-list" in args:
            return 0, "0"
        return 0, ""

    config = make_config(github_token=token)
    ticket = make_ticket(9)

    with caplog.at_level(logging.DEBUG, logger="ticket_engine.local_worker"):
        worker = LocalWorker(
            config=config,
            github_client=make_fake_github(),
            agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
            git_runner=recording_runner,
        )
        worker.run_one(make_repo_entry(), ticket)

    all_args_text = " ".join(str(a) for args in git_calls for a in args)
    assert token not in all_args_text, "Raw token must not appear in any git arg list"
    assert token_b64 not in all_args_text, "Base64 token must not appear in any git arg list"

    log_text = " ".join(r.getMessage() for r in caplog.records)
    assert token not in log_text, "Raw token must not appear in logs"
    assert token_b64 not in log_text, "Base64 token must not appear in logs"

    # Push env must be present and contain the token only through the auth header
    push_envs = [e for c, e in zip(git_calls, git_envs) if e and "push" in c]
    assert push_envs, "Push calls must include an env dict"
    auth_val = push_envs[0].get("GIT_CONFIG_VALUE_0", "")
    assert token_b64 in auth_val, "Push env must carry base64-encoded token in auth header"


# ---------------------------------------------------------------------------
# AC4: Never remove a worktree with unpushed commits
# ---------------------------------------------------------------------------

def test_worktree_not_removed_when_unpushed_commits():
    """When rev-list returns 2, worktree remove is never called."""
    git_calls = []
    runner = _make_git_runner(git_calls, {"rev-list": (0, "2")})

    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
        git_runner=runner,
    )
    worker.run_one(make_repo_entry(), make_ticket(9))

    remove_calls = [c for c in git_calls if "worktree" in c and "remove" in c]
    assert not remove_calls, "Worktree must not be removed when there are unpushed commits"


def test_force_never_passed_to_worktree_remove():
    """--force is never passed to git worktree remove."""
    git_calls = []

    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
        git_runner=_make_git_runner(git_calls),
    )
    worker.run_one(make_repo_entry(), make_ticket(9))

    for call in git_calls:
        if "worktree" in call and "remove" in call:
            assert "--force" not in call, "--force must never be passed to worktree remove"


# ---------------------------------------------------------------------------
# AC5: Skill section 6b is consistent; run_one works any runner
# ---------------------------------------------------------------------------

def test_skill_6b_no_push_the_work_no_continue():
    """Ticket skill §6b contains no 'push the work' and no '--continue'."""
    import re as _re

    from ticket_engine.prompt import load_ticket_skill
    skill = load_ticket_skill()

    # Extract §6b text (from "## 6b." heading to the next "---" separator or heading).
    match = _re.search(
        r"^## 6b\..*?(?=\n---|\n## |\Z)",
        skill,
        _re.MULTILINE | _re.DOTALL,
    )
    assert match, "§6b section must exist in ticket_skill.md"
    section_6b = match.group(0)

    assert "push the work" not in section_6b, \
        "§6b must not say 'push the work' — use 'commit the work'"
    assert "--continue" not in section_6b, \
        "§6b must not mention '--continue' — resuming uses a fresh session on the branch"


def test_run_one_works_any_runner():
    """run_one processes a ticket with runner='any' without skipping it."""
    worked = []

    def run_fn(args, cwd=None):
        worked.append(args)
        return 0, '{"status": "SUCCESS"}'

    ticket = make_ticket(9, runner="any", effort="phase-1")
    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=_make_git_runner([]),
    )
    result = worker.run_one(make_repo_entry(), ticket)

    assert result is True, "run_one must succeed for runner='any'"
    assert worked, "agy must be invoked for runner='any'"


# ---------------------------------------------------------------------------
# Ticket 29 AC1/AC2: PR adapter used only when the worktree ticket is done
# ---------------------------------------------------------------------------

def test_pr_opened_when_worktree_ticket_is_done():
    """After a run leaving Status: done, create_pull_request is called once,
    with head/base/title/body exactly as ticket 29 AC2 specifies."""
    ticket = make_ticket(9, effort="phase-1")
    ticket.title = "Add feature"
    done_content = "# 09: Add feature\n**Status:** done\n\n## Comments\n"

    mock_github = make_fake_github()
    mock_github.find_open_pr.return_value = None
    mock_github.create_pull_request.return_value = 42

    worker = LocalWorker(
        config=make_config(),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
        git_runner=_make_git_runner([]),
        read_ticket_fn=lambda p: done_content,
    )
    success = worker.run_one(make_repo_entry(repo="owner/repo", path="/fake/repo"), ticket)

    assert success
    mock_github.create_pull_request.assert_called_once()
    kwargs = mock_github.create_pull_request.call_args.kwargs
    ticket_branch = "ticket/phase-1-09-ticket-9"
    ticket_path = ".scratch/phase-1/issues/09-ticket-9.md"
    assert kwargs["head"] == ticket_branch
    assert kwargs["base"] == "master"
    assert kwargs["title"] == "phase-1-09: Add feature"
    assert kwargs["body"] == (
        f"Ticket 09 worked by the box.\n\nTicket file: {ticket_path}\nBranch: {ticket_branch}"
    )


def test_no_second_pr_when_one_already_open():
    """When find_open_pr already returns a number, create_pull_request is not called."""
    ticket = make_ticket(9, effort="phase-1")
    done_content = "# 09: Add feature\n**Status:** done\n"

    mock_github = make_fake_github()
    mock_github.find_open_pr.return_value = 7

    worker = LocalWorker(
        config=make_config(),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
        git_runner=_make_git_runner([]),
        read_ticket_fn=lambda p: done_content,
    )
    success = worker.run_one(make_repo_entry(), ticket)

    assert success
    mock_github.create_pull_request.assert_not_called()


def test_no_pr_when_worktree_ticket_left_in_progress():
    """A run whose worktree ticket file is left at Status: in-progress opens no PR."""
    ticket = make_ticket(9, effort="phase-1")
    in_progress_content = "# 09: Add feature\n**Status:** in-progress\n"

    mock_github = make_fake_github()

    worker = LocalWorker(
        config=make_config(),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
        git_runner=_make_git_runner([]),
        read_ticket_fn=lambda p: in_progress_content,
    )
    worker.run_one(make_repo_entry(), ticket)

    mock_github.create_pull_request.assert_not_called()


# ---------------------------------------------------------------------------
# Ticket 29 AC3/AC4: a box that has lost its claim drops the ticket
# ---------------------------------------------------------------------------

def test_lost_claim_gone_drops_worktree_without_pushing_or_pr(caplog):
    """When the claim branch is gone before a quota-error resume, run_one
    force-removes the worktree, pushes and PRs nothing more, and returns False."""
    import logging

    reset_time = "2026-09-23T06:00:00Z"
    run_calls = []

    def run_fn(args, cwd=None):
        run_calls.append(list(args))
        return 1, json.dumps(
            {"status": "ERROR", "message": "quota exceeded", "reset_at": reset_time}
        )

    ticket = make_ticket(9, effort="phase-1")
    git_calls = []
    mock_github = make_fake_github()
    mock_github.list_claim_branches.return_value = []  # claim branch gone

    with caplog.at_level(logging.INFO, logger="ticket_engine.local_worker"):
        worker = LocalWorker(
            config=make_config(),
            github_client=mock_github,
            agy_driver=AgyDriver(run_fn=run_fn),
            git_runner=_make_git_runner(git_calls),
            sleep_fn=lambda s: None,
            read_ticket_fn=lambda p: "# 09: Test\n**Status:** in-progress\n",
        )
        success = worker.run_one(make_repo_entry(), ticket)

    assert success is False
    mock_github.create_pull_request.assert_not_called()
    p_calls = [a for a in run_calls if "-p" in a]
    assert len(p_calls) == 1, "No resume attempt once the claim is lost"

    push_calls = [c for c in git_calls if "push" in c]
    assert not push_calls, "No push once the claim is lost"

    remove_calls = [c for c in git_calls if "worktree" in c and "remove" in c]
    assert remove_calls, "git worktree remove must be called"
    assert "--force" in remove_calls[0], "The lost-claim removal must pass --force"

    assert any("lost claim" in r.getMessage().lower() for r in caplog.records)


def test_lost_claim_jules_holds_it_drops_worktree_without_pushing_or_pr(caplog):
    """When the claim branch's ticket file reads Claimed-by: jules, the box
    drops the ticket the same way as when the claim branch is gone."""
    import logging

    reset_time = "2026-09-23T06:00:00Z"
    run_calls = []

    def run_fn(args, cwd=None):
        run_calls.append(list(args))
        return 1, json.dumps(
            {"status": "ERROR", "message": "quota exceeded", "reset_at": reset_time}
        )

    ticket = make_ticket(9, effort="phase-1")
    git_calls = []
    mock_github = make_fake_github()
    mock_github.list_claim_branches.return_value = ["claim/phase-1/09"]
    mock_github.get_file_contents.return_value = {
        "content": "# 09: Test\n**Status:** in-progress\n**Claimed-by:** jules\n",
        "sha": "def456",
    }

    with caplog.at_level(logging.INFO, logger="ticket_engine.local_worker"):
        worker = LocalWorker(
            config=make_config(),
            github_client=mock_github,
            agy_driver=AgyDriver(run_fn=run_fn),
            git_runner=_make_git_runner(git_calls),
            sleep_fn=lambda s: None,
            read_ticket_fn=lambda p: "# 09: Test\n**Status:** in-progress\n",
        )
        success = worker.run_one(make_repo_entry(), ticket)

    assert success is False
    mock_github.create_pull_request.assert_not_called()
    p_calls = [a for a in run_calls if "-p" in a]
    assert len(p_calls) == 1, "No resume attempt once Jules holds the claim"

    push_calls = [c for c in git_calls if "push" in c]
    assert not push_calls, "No push once Jules holds the claim"

    remove_calls = [c for c in git_calls if "worktree" in c and "remove" in c]
    assert remove_calls, "git worktree remove must be called"
    assert "--force" in remove_calls[0], "The lost-claim removal must pass --force"

    assert any("lost claim" in r.getMessage().lower() for r in caplog.records)


def test_pusher_stops_pushing_once_claim_is_lost_mid_run():
    """The timer pusher's push check calls claim_still_mine before every push
    and stops pushing once the claim list no longer contains the claim branch."""
    import threading

    from ticket_engine.local_worker import CheckpointPusher

    git_calls = []
    mock_github = make_fake_github()
    mock_github.list_claim_branches.side_effect = [
        ["claim/phase-1/09"],  # first check (t=0): still claimed
        [],  # second check: claim gone
        [],
    ]

    worker = LocalWorker(
        config=make_config(),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, '{"status": "SUCCESS"}')),
        git_runner=_make_git_runner(git_calls),
    )

    stop_event = threading.Event()
    slept = []

    def sleep_fn(secs):
        slept.append(secs)
        if len(slept) >= 2:
            stop_event.set()

    def push_fn():
        worker._push_if_claimed(
            "owner/repo",
            "claim/phase-1/09",
            ".scratch/phase-1/issues/09-ticket-9.md",
            "/fake/worktree",
            "ticket/phase-1-09-ticket-9",
            None,
        )

    pusher = CheckpointPusher(push_fn=push_fn, interval_s=20 * 60, sleep_fn=sleep_fn)
    pusher.run_until(stop_event)

    push_calls = [c for c in git_calls if "push" in c]
    assert len(push_calls) == 1, "Only the first push (while claimed) should have happened"


# ---------------------------------------------------------------------------
# Ticket 30 AC1: fix_ci reads check runs, prompts naming only failing checks
# ---------------------------------------------------------------------------

def test_fix_ci_prompt_names_only_failing_checks():
    """fix_ci reads the PR head's check runs and adds a CI FAILED section
    naming one `- <check name>` line per check whose conclusion is failure."""
    prompts_seen = []
    git_calls = []

    def run_fn(args, cwd=None):
        p_idx = args.index("-p")
        prompts_seen.append(args[p_idx + 1])
        return 0, json.dumps({"status": "SUCCESS"})

    mock_github = make_fake_github()
    mock_github.list_check_runs.return_value = [
        ("integrity-gate", "failure"),
        ("pytest", "success"),
    ]

    ticket = make_ticket(30, effort="box-primary-worker")
    entry = make_repo_entry(repo="owner/repo")

    worker = LocalWorker(
        config=make_config(),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=_make_git_runner(git_calls),
    )
    worker.fix_ci(entry, ticket, pr_number=12)

    assert prompts_seen, "agy must be invoked"
    prompt = prompts_seen[0]
    assert "## CI FAILED — FIX IT" in prompt
    assert "- integrity-gate" in prompt
    assert "- pytest" not in prompt
    mock_github.list_check_runs.assert_called_once()
    assert mock_github.list_check_runs.call_args.args[0] == "owner/repo"

    push_calls = [c for c in git_calls if "push" in c]
    assert push_calls, "fix_ci must push the branch after agy runs"


# ---------------------------------------------------------------------------
# Ticket 30 AC2: a waiting run gets auto-replies, then escalates kept_asking
# ---------------------------------------------------------------------------

def test_waiting_gets_auto_replies_then_escalates_kept_asking():
    """Three waiting outcomes: two auto-reply prompts, then a kept_asking escalation."""
    prompts_seen = []
    git_calls = []
    written: dict[str, str] = {}

    def run_fn(args, cwd=None):
        p_idx = args.index("-p")
        prompts_seen.append(args[p_idx + 1])
        return 0, json.dumps({"status": "WAITING"})

    ticket = make_ticket(9, effort="phase-1")
    mock_github = make_fake_github()
    mock_github.find_open_pr.return_value = None
    mock_github.create_pull_request.return_value = 55
    mock_github.find_open_issue.return_value = None

    worker = LocalWorker(
        config=make_config(),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=_make_git_runner(git_calls),
        sleep_fn=lambda s: None,
        read_ticket_fn=lambda p: "# 09: Test\n**Status:** in-progress\n\n## Comments\n",
        write_ticket_fn=lambda p, c: written.__setitem__("content", c),
    )
    success = worker.run_one(make_repo_entry(repo="owner/repo"), ticket)

    assert success is False
    assert len(prompts_seen) == 3, "initial run + two allowed auto-replies (max_auto_replies=2)"
    for prompt in prompts_seen[1:]:
        assert prompt.endswith(AUTO_REPLY_TEXT)

    assert "**Status:** blocked" in written["content"]
    assert "## Escalation —" in written["content"]

    mock_github.create_pull_request.assert_called_once()
    assert mock_github.create_pull_request.call_args.kwargs["draft"] is True

    mock_github.add_issue_labels.assert_called_once_with(
        "owner/repo", 55, ["engine:escalated"]
    )
    mock_github.create_issue.assert_called_once()
    title = mock_github.create_issue.call_args.args[1]
    assert "kept_asking" in mock_github.create_issue.call_args.args[2]
    assert title.startswith("Escalation: phase-1-09")


# ---------------------------------------------------------------------------
# Ticket 30 AC3: resumes exhausted escalates with the five documented effects
# ---------------------------------------------------------------------------

def test_resumes_exhausted_escalates_with_all_five_effects():
    """max_resumes_per_ticket failed/timeout runs: brief written, committed,
    pushed, a draft PR opened, and the PR labelled engine:escalated."""
    git_calls = []
    written: dict[str, str] = {}
    call_count = [0]

    def run_fn(args, cwd=None):
        call_count[0] += 1
        return 1, json.dumps({"status": "ERROR", "message": "boom"})

    ticket = make_ticket(9, effort="phase-1")
    mock_github = make_fake_github()
    mock_github.find_open_pr.return_value = None
    mock_github.create_pull_request.return_value = 77
    mock_github.find_open_issue.return_value = None

    worker = LocalWorker(
        config=make_config(max_resumes_per_ticket=3),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=_make_git_runner(git_calls),
        sleep_fn=lambda s: None,
        read_ticket_fn=lambda p: "# 09: Test\n**Status:** in-progress\n\n## Comments\n",
        write_ticket_fn=lambda p, c: written.__setitem__("content", c),
    )
    success = worker.run_one(make_repo_entry(repo="owner/repo"), ticket)

    assert success is False
    assert call_count[0] == 3, "initial run plus two resumes before escalating"

    assert "**Status:** blocked" in written["content"]
    assert "## Escalation —" in written["content"]

    commit_calls = [c for c in git_calls if "commit" in c and "-m" in c]
    assert commit_calls, "escalation commit must happen"
    msg_idx = commit_calls[0].index("-m")
    assert commit_calls[0][msg_idx + 1] == "Escalate 09: resumes exhausted"

    push_calls = [c for c in git_calls if "push" in c]
    assert push_calls, "escalation branch must be pushed"

    mock_github.create_pull_request.assert_called_once()
    assert mock_github.create_pull_request.call_args.kwargs["draft"] is True

    mock_github.add_issue_labels.assert_called_once_with(
        "owner/repo", 77, ["engine:escalated"]
    )
    mock_github.create_issue.assert_called_once()
    assert mock_github.create_issue.call_args.args[0] == "owner/repo"
    assert "resumes_exhausted" in mock_github.create_issue.call_args.args[2]


def test_resumes_exhausted_converts_existing_open_pr_to_draft():
    """When a PR is already open on the ticket branch, escalation converts it
    to draft instead of opening a second one."""
    git_calls = []

    def run_fn(args, cwd=None):
        return 1, json.dumps({"status": "ERROR", "message": "boom"})

    ticket = make_ticket(9, effort="phase-1")
    mock_github = make_fake_github()
    mock_github.find_open_pr.return_value = 88
    mock_github.find_open_issue.return_value = None

    worker = LocalWorker(
        config=make_config(max_resumes_per_ticket=1),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=_make_git_runner(git_calls),
        sleep_fn=lambda s: None,
        read_ticket_fn=lambda p: "# 09: Test\n**Status:** in-progress\n\n## Comments\n",
        write_ticket_fn=lambda p, c: None,
    )
    success = worker.run_one(make_repo_entry(repo="owner/repo"), ticket)

    assert success is False
    mock_github.create_pull_request.assert_not_called()
    mock_github.convert_pr_to_draft.assert_called_once_with("owner/repo", 88)
    mock_github.add_issue_labels.assert_called_once_with(
        "owner/repo", 88, ["engine:escalated"]
    )


# ---------------------------------------------------------------------------
# Ticket 30 AC4: one escalation issue per ticket
# ---------------------------------------------------------------------------

def test_second_escalation_of_same_ticket_creates_no_second_issue():
    """A second escalation finds the still-open issue and does not create another."""
    git_calls = []

    def run_fn(args, cwd=None):
        return 1, json.dumps({"status": "ERROR", "message": "boom"})

    ticket = make_ticket(9, effort="phase-1")
    mock_github = make_fake_github()
    mock_github.find_open_pr.return_value = 88
    mock_github.find_open_issue.return_value = 200  # already open

    worker = LocalWorker(
        config=make_config(max_resumes_per_ticket=1),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=_make_git_runner(git_calls),
        sleep_fn=lambda s: None,
        read_ticket_fn=lambda p: "# 09: Test\n**Status:** in-progress\n\n## Comments\n",
        write_ticket_fn=lambda p, c: None,
    )
    worker.run_one(make_repo_entry(repo="owner/repo"), ticket)

    mock_github.create_issue.assert_not_called()


# ---------------------------------------------------------------------------
# Ticket 30 AC5: quota outcomes never count toward resumes or escalate
# ---------------------------------------------------------------------------

def test_quota_outcomes_never_count_toward_resumes_or_escalate():
    """Five quota outcomes followed by success: no resume counted (even with
    max_resumes_per_ticket=1) and no escalation effect recorded."""
    outcomes = ["quota"] * 5 + ["success"]
    state = {"i": 0}

    def run_fn(args, cwd=None):
        outcome = outcomes[state["i"]]
        state["i"] += 1
        if outcome == "quota":
            return 1, json.dumps(
                {
                    "status": "ERROR",
                    "message": "quota exceeded",
                    "reset_at": "2026-09-23T06:00:00Z",
                }
            )
        return 0, json.dumps({"status": "SUCCESS"})

    ticket = make_ticket(9, effort="phase-1")
    mock_github = make_fake_github()

    worker = LocalWorker(
        config=make_config(max_resumes_per_ticket=1),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=_make_git_runner([]),
        sleep_fn=lambda s: None,
        read_ticket_fn=lambda p: "# 09: Test\n**Status:** in-progress\n\n## Comments\n",
    )
    success = worker.run_one(make_repo_entry(), ticket)

    assert success is True
    assert state["i"] == 6, "five quota attempts plus the final success"
    mock_github.create_issue.assert_not_called()
    mock_github.convert_pr_to_draft.assert_not_called()
    mock_github.create_pull_request.assert_not_called()


# ---------------------------------------------------------------------------
# Ticket 40: LocalWorker falls back to Sonnet when agy is out of quota
# ---------------------------------------------------------------------------

class _FakeStepDriver:
    """A bare test double exposing .start and recording every call's prompt/cwd.

    Results are queued one per call; the last result repeats for any call
    past the end of the queue.
    """

    def __init__(self, results):
        self._results = list(results)
        self.calls: list[tuple[str, str | None]] = []

    def start(self, prompt: str, cwd: str | None = None):
        self.calls.append((prompt, cwd))
        idx = min(len(self.calls) - 1, len(self._results) - 1)
        return self._results[idx]


def test_start_with_fallback_no_sonnet_configured_passes_quota_through():
    """With sonnet_driver=None (the default), a quota result passes through unchanged."""
    quota_result = AgyResult(outcome="quota", quota_error=True)
    agy = _FakeStepDriver([quota_result])

    worker = LocalWorker(
        config=make_config(), github_client=make_fake_github(), agy_driver=agy
    )
    result = worker._start_with_fallback("prompt", cwd="/worktree")

    assert result is quota_result
    assert agy.calls == [("prompt", "/worktree")]


def test_start_with_fallback_agy_quota_sonnet_succeeds():
    """agy quota, Sonnet configured and succeeds: the caller sees Sonnet's result."""
    agy = _FakeStepDriver([AgyResult(outcome="quota", quota_error=True)])
    success_result = SonnetResult(outcome="success", success=True)
    sonnet = _FakeStepDriver([success_result])

    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=agy,
        sonnet_driver=sonnet,
    )
    result = worker._start_with_fallback("prompt", cwd="/worktree")

    assert result is success_result
    assert agy.calls == [("prompt", "/worktree")]
    assert sonnet.calls == [("prompt", "/worktree")]


def test_start_with_fallback_agy_quota_sonnet_also_quota():
    """agy quota, Sonnet also quota: the combined result is Sonnet's quota result,
    with each driver called exactly once (no ping-pong)."""
    agy_quota = AgyResult(outcome="quota", quota_error=True)
    sonnet_quota = SonnetResult(outcome="quota", quota_error=True)
    agy = _FakeStepDriver([agy_quota])
    sonnet = _FakeStepDriver([sonnet_quota])

    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=agy,
        sonnet_driver=sonnet,
    )
    result = worker._start_with_fallback("prompt", cwd="/worktree")

    assert result is sonnet_quota
    assert result.quota_error is True
    assert len(agy.calls) == 1
    assert len(sonnet.calls) == 1


@pytest.mark.parametrize("outcome", ["success", "waiting", "timeout", "failed"])
def test_start_with_fallback_non_quota_never_calls_sonnet(outcome):
    """agy succeeds or fails for a non-quota reason: Sonnet is never called."""
    agy_result = AgyResult(outcome=outcome, success=(outcome == "success"))
    agy = _FakeStepDriver([agy_result])
    sonnet = _FakeStepDriver([SonnetResult(outcome="success", success=True)])

    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=agy,
        sonnet_driver=sonnet,
    )
    result = worker._start_with_fallback("prompt", cwd="/worktree")

    assert result is agy_result
    assert sonnet.calls == []


def test_run_one_first_attempt_falls_back_to_sonnet_on_quota():
    """run_one's first attempt goes through _start_with_fallback: an agy quota
    result there also reaches sonnet_driver.start, with the identical prompt/cwd."""
    ticket = make_ticket(9)
    agy = _FakeStepDriver([AgyResult(outcome="quota", quota_error=True)])
    sonnet = _FakeStepDriver([SonnetResult(outcome="success", success=True)])

    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=agy,
        sonnet_driver=sonnet,
        git_runner=_make_git_runner([]),
        sleep_fn=lambda s: None,
    )
    success = worker.run_one(make_repo_entry(), ticket)

    assert success is True
    assert len(agy.calls) == 1
    assert sonnet.calls == agy.calls


def test_resume_from_checkpoint_falls_back_to_sonnet_on_quota():
    """_resume_from_checkpoint goes through _start_with_fallback: an agy quota
    result on resume also reaches sonnet_driver.start."""
    ticket = make_ticket(9)
    # First attempt fails outright (non-quota, no fallback); the resume attempt
    # gets a quota result from agy, which falls back to Sonnet.
    agy = _FakeStepDriver(
        [AgyResult(outcome="failed"), AgyResult(outcome="quota", quota_error=True)]
    )
    sonnet = _FakeStepDriver([SonnetResult(outcome="success", success=True)])

    worker = LocalWorker(
        config=make_config(max_resumes_per_ticket=3),
        github_client=make_fake_github(),
        agy_driver=agy,
        sonnet_driver=sonnet,
        git_runner=_make_git_runner([]),
        sleep_fn=lambda s: None,
        read_ticket_fn=lambda p: "# 09: Test\n**Status:** in-progress\n\n## Comments\n",
    )
    success = worker.run_one(make_repo_entry(), ticket)

    assert success is True
    assert len(agy.calls) == 2
    assert len(sonnet.calls) == 1
    assert sonnet.calls[0] == agy.calls[1]


def test_send_auto_reply_falls_back_to_sonnet_on_quota():
    """_send_auto_reply goes through _start_with_fallback: an agy quota result
    on the auto-reply attempt also reaches sonnet_driver.start."""
    ticket = make_ticket(9)
    # First attempt waits (non-quota); the auto-reply attempt gets a quota
    # result from agy, which falls back to Sonnet.
    agy = _FakeStepDriver(
        [AgyResult(outcome="waiting"), AgyResult(outcome="quota", quota_error=True)]
    )
    sonnet = _FakeStepDriver([SonnetResult(outcome="success", success=True)])

    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=agy,
        sonnet_driver=sonnet,
        git_runner=_make_git_runner([]),
        sleep_fn=lambda s: None,
        read_ticket_fn=lambda p: "# 09: Test\n**Status:** in-progress\n\n## Comments\n",
    )
    success = worker.run_one(make_repo_entry(), ticket)

    assert success is True
    assert len(agy.calls) == 2
    assert len(sonnet.calls) == 1
    assert sonnet.calls[0][0].endswith(AUTO_REPLY_TEXT)
    assert sonnet.calls[0] == agy.calls[1]


def test_fix_ci_falls_back_to_sonnet_on_quota():
    """fix_ci goes through _start_with_fallback: an agy quota result there also
    reaches sonnet_driver.start with the identical prompt/cwd."""
    ticket = make_ticket(30, effort="box-primary-worker")
    entry = make_repo_entry(repo="owner/repo")
    mock_github = make_fake_github()
    mock_github.list_check_runs.return_value = [("integrity-gate", "failure")]

    agy = _FakeStepDriver([AgyResult(outcome="quota", quota_error=True)])
    success_result = SonnetResult(outcome="success", success=True)
    sonnet = _FakeStepDriver([success_result])

    worker = LocalWorker(
        config=make_config(),
        github_client=mock_github,
        agy_driver=agy,
        sonnet_driver=sonnet,
        git_runner=_make_git_runner([]),
    )
    result = worker.fix_ci(entry, ticket, pr_number=12)

    assert result is success_result
    assert len(agy.calls) == 1
    assert sonnet.calls == agy.calls


def test_no_direct_agy_driver_start_calls_outside_fallback():
    """local_worker.py contains no remaining direct call to agy_driver.start
    outside _start_with_fallback itself."""
    import inspect

    source = inspect.getsource(LocalWorker)
    calls = [
        line
        for line in source.splitlines()
        if "self.agy_driver.start(" in line
    ]
    assert len(calls) == 1, "the only direct call must be inside _start_with_fallback"


# ---------------------------------------------------------------------------
# Git calls have a timeout (box setup follow-up): a git command waiting on a
# credential prompt nobody can see must not hang the worker forever.
# ---------------------------------------------------------------------------

def test_local_worker_default_git_runner_gives_up_after_its_timeout():
    import sys
    import time

    from ticket_engine import local_worker

    started = time.monotonic()
    rc, out = local_worker._default_git_runner(
        [sys.executable, "-c", "import time; time.sleep(30)"], None, None, timeout=0.5
    )
    assert rc != 0
    assert "timed out after 0.5s" in out
    assert time.monotonic() - started < 10


def test_local_worker_runs_git_with_the_configured_timeout(monkeypatch):
    from unittest.mock import MagicMock

    from ticket_engine import local_worker

    seen: list[object] = []

    def fake_runner(args, cwd=None, env=None, timeout=None):
        seen.append(timeout)
        return 0, ""

    monkeypatch.setattr(local_worker, "_default_git_runner", fake_runner)
    worker = LocalWorker(
        config=LocalWorkerConfig(git_timeout_seconds=42),
        github_client=MagicMock(),
        agy_driver=MagicMock(),
    )

    worker._git_runner(["git", "status"], None, None)

    assert seen == [42]


# ---------------------------------------------------------------------------
# Ticket 45: repo-relative ticket path everywhere
# ---------------------------------------------------------------------------

def test_ticket_path_str_strips_an_absolute_windows_path():
    """_ticket_path_str returns the repo-relative, /-separated path."""
    t1 = Ticket(
        number=50,
        title="x",
        slug="x",
        status="ready-for-agent",
        effort="e",
        path=pathlib.Path(r"C:\Users\agent\projects\Slackbot\.scratch\e\issues\50-x.md"),
    )
    assert _ticket_path_str(t1, "e") == ".scratch/e/issues/50-x.md"

    t2 = Ticket(
        number=7,
        title="y",
        slug="y",
        status="ready-for-agent",
        effort="e",
        path=None,
    )
    assert _ticket_path_str(t2, "e") == ".scratch/e/issues/07-y.md"


def test_run_one_uses_repo_relative_ticket_path_with_a_loaded_ticket(tmp_path):
    """run_one sends GitHub the relative path and prompt names the relative path."""
    ticket_file = tmp_path / ".scratch" / "e" / "issues" / "50-x.md"
    ticket_file.parent.mkdir(parents=True, exist_ok=True)
    ticket_content = (
        "# 50: Test ticket\n\n"
        "**Status:** ready-for-agent\n\n"
        "**Runner:** any\n\n"
        "## Acceptance criteria\n\n"
        "- [ ] Criterion 1\n\n"
        "## Comments\n"
    )
    ticket_file.write_text(ticket_content, encoding="utf-8")

    loaded_tickets = _load_tickets_from_path(tmp_path)
    assert len(loaded_tickets) == 1
    ticket = loaded_tickets[0]

    mock_github = make_fake_github()
    args_seen = []

    def run_fn(args, cwd=None):
        args_seen.append(args)
        return 0, json.dumps({"status": "SUCCESS"})

    done_content = (
        "# 50: Test ticket\n\n"
        "**Status:** done\n\n"
        "**Runner:** any\n\n"
        "## Acceptance criteria\n\n"
        "- [x] Criterion 1\n\n"
        "## Comments\n"
    )

    worker = LocalWorker(
        config=make_config(),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=_make_git_runner([]),
        read_ticket_fn=lambda p: done_content,
    )
    entry = make_repo_entry(path=str(tmp_path))
    success = worker.run_one(entry, ticket)
    assert success

    assert mock_github.get_file_contents.called
    for call in mock_github.get_file_contents.call_args_list:
        path_arg = call.args[1] if len(call.args) > 1 else call.kwargs.get("path")
        assert path_arg == ".scratch/e/issues/50-x.md"

    assert mock_github.commit_file_change.called
    for call in mock_github.commit_file_change.call_args_list:
        assert call.kwargs.get("path") == ".scratch/e/issues/50-x.md"

    p_calls = [a for a in args_seen if "-p" in a]
    assert p_calls, "agy should have been invoked with -p"
    p_idx = p_calls[0].index("-p")
    prompt = p_calls[0][p_idx + 1]
    assert ".scratch/e/issues/50-x.md" in prompt
    assert str(tmp_path) not in prompt


def test_escalation_writes_brief_into_the_worktree_copy_of_a_loaded_ticket(tmp_path):
    """Escalation writes into the worktree copy of a loaded ticket and commits."""
    ticket_file = tmp_path / ".scratch" / "e" / "issues" / "50-x.md"
    ticket_file.parent.mkdir(parents=True, exist_ok=True)
    ticket_content = (
        "# 50: Test ticket\n\n"
        "**Status:** ready-for-agent\n\n"
        "**Runner:** any\n\n"
        "## Acceptance criteria\n\n"
        "- [ ] Criterion 1\n\n"
        "## Comments\n"
    )
    ticket_file.write_text(ticket_content, encoding="utf-8")

    loaded_tickets = _load_tickets_from_path(tmp_path)
    assert len(loaded_tickets) == 1
    ticket = loaded_tickets[0]

    git_calls = []
    recorded_write_paths = []

    def run_fn(args, cwd=None):
        return 1, json.dumps({"status": "ERROR", "message": "boom"})

    mock_github = make_fake_github()
    mock_github.find_open_pr.return_value = None
    mock_github.create_pull_request.return_value = 77
    mock_github.find_open_issue.return_value = None

    def write_ticket_fn(p, c):
        recorded_write_paths.append(pathlib.Path(p))

    worker = LocalWorker(
        config=make_config(max_resumes_per_ticket=3),
        github_client=mock_github,
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=_make_git_runner(git_calls),
        sleep_fn=lambda s: None,
        read_ticket_fn=lambda p: "# 50: Test\n**Status:** in-progress\n\n## Comments\n",
        write_ticket_fn=write_ticket_fn,
    )
    success = worker.run_one(make_repo_entry(path=str(tmp_path), repo="owner/repo"), ticket)

    assert success is False
    worktree_add_calls = [c for c in git_calls if "worktree" in c and "add" in c]
    assert worktree_add_calls
    worktree_path = _extract_worktree_path(worktree_add_calls[0])
    assert worktree_path

    assert recorded_write_paths
    assert recorded_write_paths[-1] == pathlib.Path(worktree_path) / ".scratch/e/issues/50-x.md"

    git_add_calls = [c for c in git_calls if "add" in c and "worktree" not in c]
    assert git_add_calls
    assert git_add_calls[-1][-1] == ".scratch/e/issues/50-x.md"

    mock_github.create_pull_request.assert_called_once()
    assert mock_github.create_pull_request.call_args.kwargs["draft"] is True



# ---------------------------------------------------------------------------
# Ticket 47: the box resumes its own claim; a worktree that cannot be made
# never gets agy started in it.
# ---------------------------------------------------------------------------

def _claim_branch_content(claimed_by: str | None) -> dict:
    line = f"**Claimed-by:** {claimed_by}\n\n" if claimed_by else ""
    return {
        "content": f"# 09: Ticket 9\n\n**Status:** ready-for-agent\n\n{line}",
        "sha": "def456",
    }


def test_run_one_resumes_its_own_claim_when_the_claim_branch_already_exists():
    """A claim branch that already says Claimed-by: box is the box's own claim
    (a quota pause or a restart left it): run_one works it, and does not
    re-commit Claimed-by."""
    agy_calls = []

    def run_fn(args, cwd=None):
        agy_calls.append(args)
        return 0, json.dumps({"status": "SUCCESS"})

    gh = make_fake_github(claim_result=False)
    gh.get_file_contents.return_value = _claim_branch_content("box")
    worker = LocalWorker(
        config=make_config(),
        github_client=gh,
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=_make_git_runner([]),
    )

    result = worker.run_one(make_repo_entry(), make_ticket(9))

    assert result is True
    assert len(agy_calls) == 1, "agy must run on the box's own existing claim"
    gh.commit_file_change.assert_not_called()


def test_run_one_skips_a_claim_branch_claimed_by_jules():
    """An existing claim branch that says Claimed-by: jules is not the box's."""
    agy_calls = []
    gh = make_fake_github(claim_result=False)
    gh.get_file_contents.return_value = _claim_branch_content("jules")
    worker = LocalWorker(
        config=make_config(),
        github_client=gh,
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: agy_calls.append(a) or (0, "{}")),
        git_runner=_make_git_runner([]),
    )

    assert worker.run_one(make_repo_entry(), make_ticket(9)) is False
    assert agy_calls == []


def test_failed_worktree_add_never_starts_agy(caplog):
    """If git cannot create the worktree, agy is never started in a folder
    that does not exist, nothing is pushed, and git's message is logged."""
    agy_calls = []
    git_calls: list = []
    runner = _make_git_runner(
        git_calls, {"add": (255, "fatal: a branch named 'x' already exists")}
    )
    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: agy_calls.append(a) or (0, "{}")),
        git_runner=runner,
    )

    with caplog.at_level("ERROR", logger="ticket_engine.local_worker"):
        result = worker.run_one(make_repo_entry(), make_ticket(9), box_mode=True)

    assert result is False
    assert agy_calls == []
    assert not [c for c in git_calls if "push" in c]
    assert "fatal: a branch named 'x' already exists" in caplog.text


def test_existing_worktree_folder_is_reused_not_re_added(tmp_path):
    """A worktree left in place (quota pause, restart) is reused as it is:
    no second `git worktree add`, and agy runs in that folder."""
    seen_cwds = []
    git_calls: list = []
    base = tmp_path / "wt"
    worktree = base / "ticket-phase-1-09-ticket-9"
    worktree.mkdir(parents=True)
    (worktree / ".git").write_text("gitdir: elsewhere\n", encoding="utf-8")

    worker = LocalWorker(
        config=make_config(worktree_base=str(base)),
        github_client=make_fake_github(),
        agy_driver=AgyDriver(
            run_fn=lambda a, cwd=None: seen_cwds.append(cwd) or (0, '{"status": "SUCCESS"}')
        ),
        git_runner=_make_git_runner(git_calls),
    )
    worker.run_one(make_repo_entry(), make_ticket(9))

    assert not [c for c in git_calls if "worktree" in c and "add" in c]
    assert seen_cwds == [str(worktree)]


def test_fix_ci_makes_the_worktree_before_running_agy():
    """A finished run removed its worktree, so fix_ci must create it again
    (on the existing remote ticket branch) before agy runs in it."""
    order: list[str] = []
    git_calls: list = []
    ticket_branch = "ticket/phase-1-09-ticket-9"
    base_runner = _make_git_runner(
        git_calls, {"ls-remote": (0, f"abc123\trefs/heads/{ticket_branch}\n")}
    )

    def runner(args, cwd=None, env=None):
        if "worktree" in args and "add" in args:
            order.append("worktree add")
        return base_runner(args, cwd, env)

    def run_fn(args, cwd=None):
        order.append("agy")
        return 0, json.dumps({"status": "SUCCESS"})

    gh = make_fake_github()
    gh.list_check_runs.return_value = [("tests", "failure")]
    worker = LocalWorker(
        config=make_config(),
        github_client=gh,
        agy_driver=AgyDriver(run_fn=run_fn),
        git_runner=runner,
    )
    worker.fix_ci(make_repo_entry(), make_ticket(9), 5)

    assert order == ["worktree add", "agy"]
    wt_add = next(c for c in git_calls if "worktree" in c and "add" in c)
    assert f"origin/{ticket_branch}" in wt_add


def test_fix_ci_never_starts_agy_without_a_worktree():
    agy_calls = []
    gh = make_fake_github()
    gh.list_check_runs.return_value = [("tests", "failure")]
    worker = LocalWorker(
        config=make_config(),
        github_client=gh,
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: agy_calls.append(a) or (0, "{}")),
        git_runner=_make_git_runner([], {"add": (128, "fatal: invalid reference")}),
    )

    assert worker.fix_ci(make_repo_entry(), make_ticket(9), 5) is None
    assert agy_calls == []


def test_successful_push_logs_the_branch_and_git_summary(caplog):
    import logging

    caplog.set_level(logging.INFO)
    out = "To https://github.com/o/r.git\n   c33b160..9a1f2e3  HEAD -> ticket/e-09-x\n"
    worker = LocalWorker(
        config=make_config(),
        github_client=make_fake_github(),
        agy_driver=AgyDriver(run_fn=lambda a, cwd=None: (0, "")),
        git_runner=_make_git_runner([], {"push": (0, out)}),
    )
    worker._push_branch("/wt", "ticket/e-09-x", None)
    hits = [
        r for r in caplog.records
        if r.levelno == logging.INFO
        and "Pushed ticket/e-09-x" in r.getMessage()
        and "c33b160..9a1f2e3" in r.getMessage()
    ]
    assert len(hits) == 1
