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

import pytest

from ticket_engine.agy import AgyDriver
from ticket_engine.local_config import LocalRepoEntry, LocalWorkerConfig, load_local_config
from ticket_engine.local_worker import LocalWorker
from ticket_engine.parser import Ticket

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


def make_fake_github(claim_result: bool = True) -> object:
    from unittest.mock import MagicMock
    mock = MagicMock()
    mock.get_default_branch_sha.return_value = "abc123"
    mock.create_claim_branch.return_value = claim_result
    mock.list_claim_branches.return_value = []
    return mock



# ---------------------------------------------------------------------------
# AC1: Local config and windows frontier
# ---------------------------------------------------------------------------

def test_load_local_config_from_toml(tmp_path):
    """load_local_config reads repos and agy settings from a TOML file."""
    config_file = tmp_path / "local.toml"
    config_file.write_text(
        '[agy]\nquota_url = "http://localhost:9000/status"\nquota_reserve_pct = 0.25\n\n'
        '[[repos]]\npath = "/code/myrepo"\nrepo = "owner/myrepo"\n',
        encoding="utf-8",
    )
    config = load_local_config(config_file)
    assert len(config.repos) == 1
    assert config.repos[0].path == "/code/myrepo"
    assert config.repos[0].repo == "owner/myrepo"
    assert config.agy_quota_url == "http://localhost:9000/status"
    assert config.agy_quota_reserve_pct == 0.25


def test_load_local_config_missing_file_returns_defaults(tmp_path):
    """load_local_config returns defaults when file does not exist."""
    config = load_local_config(tmp_path / "nonexistent.toml")
    assert config.repos == []
    assert config.agy_quota_reserve_pct == 0.20


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
    # Prompt must be present right after -p
    p_idx = call_args.index("-p")
    prompt_text = call_args[p_idx + 1]
    assert "Ticket 9" in prompt_text or "ticket" in prompt_text.lower()


# ---------------------------------------------------------------------------
# AC4: Quota check
# ---------------------------------------------------------------------------

def test_refuses_to_start_when_quota_below_20_pct():
    """run_one refuses to start when quota endpoint reports < 20% remaining."""
    agy_calls = []

    def run_fn(args, cwd=None):
        agy_calls.append(args)
        return 0, json.dumps({"status": "success"})

    def fetch_fn(url):
        return {"remaining_pct": 0.15, "reset_at": "2026-09-23T06:00:00Z"}

    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=run_fn, fetch_fn=fetch_fn)
    ticket = make_ticket(9)
    entry = make_repo_entry()
    config = make_config(agy_quota_url="http://localhost:8765/status", agy_quota_reserve_pct=0.20)

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
    )
    success = worker.run_one(entry, ticket)

    assert not success, "Must refuse below 20% quota"
    assert agy_calls == [], "agy must not be invoked when quota is too low"


def test_proceeds_when_quota_endpoint_unavailable():
    """run_one proceeds when quota endpoint returns None (endpoint unavailable)."""
    agy_calls = []

    def run_fn(args, cwd=None):
        agy_calls.append(args)
        return 0, json.dumps({"status": "success"})

    def fetch_fn(url):
        return None  # endpoint unavailable

    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=run_fn, fetch_fn=fetch_fn)
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

    assert success, "Must proceed when endpoint unavailable"
    assert agy_calls, "agy must be invoked when endpoint unavailable"


def test_proceeds_when_quota_above_reserve():
    """run_one proceeds when quota is at or above the reserve threshold."""

    def run_fn(args, cwd=None):
        return 0, json.dumps({"status": "success"})

    def fetch_fn(url):
        return {"remaining_pct": 0.50}  # well above 20%

    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=run_fn, fetch_fn=fetch_fn)
    ticket = make_ticket(9)
    config = make_config()

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
    )
    success = worker.run_one(make_repo_entry(), ticket)
    assert success


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
# AC6: Quota error handling
# ---------------------------------------------------------------------------

def test_quota_error_keeps_claim_and_resumes_with_continue():
    """On quota error from agy, keeps claim, waits, then resumes with agy --continue."""
    reset_time = "2026-09-23T06:00:00Z"
    run_calls = []

    def tracking_run_fn(args, cwd=None):
        run_calls.append(list(args))
        responses_iter = tracking_run_fn._responses
        code, data = next(responses_iter)
        return code, json.dumps(data)

    tracking_run_fn._responses = iter([
        (1, {"status": "quota_error", "reset_at": reset_time}),
        (0, {"status": "success"}),
    ])

    slept = []

    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=tracking_run_fn, fetch_fn=lambda url: {"remaining_pct": 0.50})
    ticket = make_ticket(9)
    config = make_config()

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
        sleep_fn=lambda secs: slept.append(secs),
    )
    success = worker.run_one(make_repo_entry(), ticket)

    assert success, "Should succeed after quota-error + resume"
    # Claim must NOT have been released (kept for resume)
    mock_github.delete_branch.assert_not_called()
    # Worker must have slept (waited for quota reset)
    assert slept, "Must sleep waiting for quota reset"
    # agy --continue must have been called
    assert any("--continue" in args for args in run_calls), "agy --continue must be called"


def test_resume_fallback_to_fresh_session_when_continue_fails():
    """If agy --continue fails, starts fresh agy session from checkpoint progress note."""
    reset_time = "2026-09-23T06:00:00Z"
    run_calls = []

    # Simulate: start -> quota error, --continue -> error, fresh start -> success
    def tracking_run_fn(args, cwd=None):
        run_calls.append(list(args))
        if "--continue" in args:
            return 1, json.dumps({"status": "error", "message": "No prior session"})
        # Count -p calls so far (including the one we just appended)
        p_calls_so_far = sum(1 for c in run_calls if "-p" in c)
        if p_calls_so_far >= 2:
            # Second -p call (fresh session)
            return 0, json.dumps({"status": "success"})
        # First -p call: quota error
        return 1, json.dumps({"status": "quota_error", "reset_at": reset_time})

    # We need a ticket file with a progress note in a worktree
    # The worker reads the ticket file from the worktree after checkout
    ticket = make_ticket(9, effort="phase-1")
    ticket_content = (
        "# 09: Test\n**Status:** in-progress\n\n## Comments\n\n"
        "Progress (2026-09-23 05:00): criteria 1-2 done.\nNext: criterion 3.\n"
    )

    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=tracking_run_fn, fetch_fn=lambda url: {"remaining_pct": 0.50})
    config = make_config()

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
        sleep_fn=lambda secs: None,
        read_ticket_fn=lambda path: ticket_content,
    )
    success = worker.run_one(make_repo_entry(), ticket)

    assert success, "Should succeed after quota-error + continue-fail + fresh session"
    # Fresh agy session must have been started (a second -p call)
    p_calls = [args for args in run_calls if "-p" in args]
    assert len(p_calls) >= 2, "A fresh agy session must be started after --continue fails"
    # The fresh session prompt must include checkpoint context
    fresh_prompt = None
    for args in p_calls[1:]:
        p_idx = args.index("-p")
        fresh_prompt = args[p_idx + 1]
        break
    assert fresh_prompt is not None
    assert "Progress" in fresh_prompt or "checkpoint" in fresh_prompt.lower() or "criteria" in fresh_prompt, (
        "Fresh session prompt must include the checkpoint progress note"
    )


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


def test_fake_agy_quota_refusal_scenario():
    """Fake agy: quota check below 20% causes refusal before start."""
    called = []

    def run_fn(args, cwd=None):
        called.append(args)
        return 0, json.dumps({"status": "success"})

    def fetch_fn(url):
        return {"remaining_pct": 0.10}  # below 20%

    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=run_fn, fetch_fn=fetch_fn)
    config = make_config(agy_quota_reserve_pct=0.20)

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
    )
    result = worker.run_one(make_repo_entry(), make_ticket(9))
    assert not result
    assert called == [], "agy must not be called when quota is too low"


def test_fake_agy_quota_stop_then_resume_scenario():
    """Fake agy: quota stop mid-run, then resumes with --continue."""
    run_calls = []

    def run_fn(args, cwd=None):
        run_calls.append(list(args))
        if "--continue" in args:
            return 0, json.dumps({"status": "success"})
        return 1, json.dumps({"status": "quota_error", "reset_at": "2026-09-23T06:00:00Z"})

    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=run_fn, fetch_fn=lambda url: {"remaining_pct": 0.80})
    config = make_config()

    worker = LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
        sleep_fn=lambda secs: None,
    )
    success = worker.run_one(make_repo_entry(), make_ticket(9))

    assert success
    assert any("--continue" in args for args in run_calls)
    # Claim was NOT released during the quota pause
    mock_github.delete_branch.assert_not_called()


def test_fake_agy_resume_fallback_to_fresh_session_scenario():
    """Fake agy: --continue fails, fresh session started from checkpoint."""
    run_calls = []
    p_call_count = [0]

    def run_fn(args, cwd=None):
        run_calls.append(list(args))
        if "--continue" in args:
            return 1, json.dumps({"status": "error", "message": "No prior session"})
        p_call_count[0] += 1
        if p_call_count[0] == 1:
            return 1, json.dumps({"status": "quota_error", "reset_at": "2026-09-23T06:00:00Z"})
        # Second start (fresh session) succeeds
        return 0, json.dumps({"status": "success"})

    ticket_content = (
        "# 09: Test\n**Status:** in-progress\n\n## Comments\n\n"
        "Progress (2026-09-23 05:00): criteria 1-2 done.\nNext: criterion 3.\n"
    )

    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=run_fn, fetch_fn=lambda url: {"remaining_pct": 0.80})
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
    fresh_p_idx = p_calls[1].index("-p")
    fresh_prompt = p_calls[1][fresh_p_idx + 1]
    # Fresh prompt must include the progress note context
    assert "Progress" in fresh_prompt or "criteria 1-2" in fresh_prompt


# ---------------------------------------------------------------------------
# AgyDriver unit tests
# ---------------------------------------------------------------------------

def test_agy_driver_read_quota_parses_remaining_pct():
    """AgyDriver.read_quota parses remaining_pct and reset_at from JSON."""
    def fetch_fn(url):
        return {"remaining_pct": 0.42, "reset_at": "2026-09-23T06:00:00Z"}

    driver = AgyDriver(fetch_fn=fetch_fn)
    info = driver.read_quota("http://localhost:8765/status")
    assert info is not None
    assert abs(info.remaining_pct - 0.42) < 0.001
    assert info.reset_at is not None
    assert info.reset_at.year == 2026


def test_agy_driver_read_quota_returns_none_when_unavailable():
    """AgyDriver.read_quota returns None when endpoint is unavailable."""
    driver = AgyDriver(fetch_fn=lambda url: None)
    assert driver.read_quota("http://localhost:8765/status") is None


def test_agy_driver_start_parses_success():
    """AgyDriver.start returns AgyResult(success=True) on returncode=0 success JSON."""
    driver = AgyDriver(run_fn=lambda args, cwd=None: (0, json.dumps({"status": "success"})))
    result = driver.start("my prompt")
    assert result.success
    assert not result.quota_error


def test_agy_driver_start_parses_quota_error():
    """AgyDriver.start returns AgyResult(quota_error=True) on quota error JSON."""
    driver = AgyDriver(
        run_fn=lambda args, cwd=None: (
            1,
            json.dumps({"status": "quota_error", "reset_at": "2026-09-23T06:00:00Z"}),
        )
    )
    result = driver.start("my prompt")
    assert not result.success
    assert result.quota_error
    assert result.reset_at is not None


def test_agy_driver_continue_session_passes_continue_flag():
    """AgyDriver.continue_session invokes agy with --continue flag."""
    seen_args = []

    def run_fn(args, cwd=None):
        seen_args.extend(args)
        return 0, json.dumps({"status": "success"})

    driver = AgyDriver(run_fn=run_fn)
    result = driver.continue_session()
    assert "--continue" in seen_args
    assert result.success


# ---------------------------------------------------------------------------
# LocalWorker.run() integration: finds first windows ticket across repos
# ---------------------------------------------------------------------------

def test_run_finds_first_windows_ticket_and_works_it():
    """LocalWorker.run() picks the first windows ticket across all repos and runs it."""
    worked = []

    def run_fn(args, cwd=None):
        worked.append(args)
        return 0, json.dumps({"status": "success"})

    tickets = [make_ticket(1, runner="windows")]
    mock_github = make_fake_github()
    driver = AgyDriver(run_fn=run_fn, fetch_fn=lambda url: None)
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
    driver = AgyDriver(run_fn=lambda a, cwd=None: (0, "{}"), fetch_fn=lambda u: None)
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
    driver = AgyDriver(run_fn=lambda a, cwd=None: (0, "{}"), fetch_fn=lambda u: None)
    config = make_config()
    return LocalWorker(
        config=config,
        github_client=mock_github,
        agy_driver=driver,
        git_runner=_make_git_runner([]),
        ticket_loader=lambda entry: tickets,
    )


def _make_git_runner(call_log: list):
    """Create a fake git runner that records calls."""

    def git_runner(args: list[str], cwd: str | None = None) -> tuple[int, str]:
        call_log.append(args)
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
