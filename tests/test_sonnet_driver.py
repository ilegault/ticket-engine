"""Tests for SonnetDriver argument list and outcome classification.

WHY THIS EXISTS
----------------
Ticket 39 and spec `.scratch/sonnet-quota-fallback/spec.md` §`sonnet.py` adapter
mandate that:
1. Every Sonnet invocation is:
   claude -p <prompt> --output-format stream-json --permission-mode bypassPermissions --permission-prompts none
   (no `--bare`, so the box's Claude subscription login is used, never a
   metered API key).
2. SonnetResult classifies outcomes into: success, quota, auth, timeout, failed.
   `waiting` is never produced, since `--permission-prompts none` guarantees
   the run always concludes.
3. Classification reads the CLI's own documented `stream-json` events (the
   final `result` message and any `system`/`api_retry` event's `error`
   category), never a guessed text pattern.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

from ticket_engine.sonnet import SonnetDriver, classify_retry_error

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "sonnet"


def test_sonnet_driver_start_exact_argument_list():
    """SonnetDriver.start calls run_fn with exact flags and prompt as a single argument, no --bare."""
    seen_calls: list[tuple[list[str], str | None]] = []

    def fake_run(args: list[str], cwd: str | None = None) -> tuple[int, str]:
        seen_calls.append((list(args), cwd))
        return 0, (FIXTURES_DIR / "success.jsonl").read_text(encoding="utf-8")

    driver = SonnetDriver(run_fn=fake_run, timeout_seconds=7200)
    prompt = "Implement ticket 39 with all acceptance criteria."
    driver.start(prompt, cwd="/worktree/path")

    assert len(seen_calls) == 1
    args, cwd = seen_calls[0]
    expected_args = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--permission-mode",
        "bypassPermissions",
        "--permission-prompts",
        "none",
    ]
    assert args == expected_args
    assert "--bare" not in args
    assert cwd == "/worktree/path"


def test_sonnet_outcome_success():
    """A final result message with subtype success and exit code 0 classifies as success."""
    output = (FIXTURES_DIR / "success.jsonl").read_text(encoding="utf-8")
    driver = SonnetDriver(run_fn=lambda args, cwd=None: (0, output))
    result = driver.start("prompt")

    assert result.outcome == "success"
    assert result.success is True
    assert result.cost_usd == 0.0431
    assert result.session_id == "sess-1"


def test_sonnet_outcome_quota():
    """An api_retry event with error rate_limit and no result line classifies as quota."""
    output = (FIXTURES_DIR / "quota.jsonl").read_text(encoding="utf-8")
    driver = SonnetDriver(run_fn=lambda args, cwd=None: (1, output))
    result = driver.start("prompt")

    assert result.outcome == "quota"
    assert result.quota_error is True
    assert result.reset_at is None


def test_sonnet_outcome_auth():
    """An api_retry event with error authentication_failed and no result line classifies as auth."""
    output = (FIXTURES_DIR / "auth.jsonl").read_text(encoding="utf-8")
    driver = SonnetDriver(run_fn=lambda args, cwd=None: (1, output))
    result = driver.start("prompt")

    assert result.outcome == "auth"


def test_sonnet_outcome_failed():
    """An api_retry event with error server_error (not a quota or auth category) classifies as failed."""
    output = (FIXTURES_DIR / "failed.jsonl").read_text(encoding="utf-8")
    driver = SonnetDriver(run_fn=lambda args, cwd=None: (1, output))
    result = driver.start("prompt")

    assert result.outcome == "failed"
    assert result.success is False
    assert result.quota_error is False


def test_sonnet_outcome_timeout_from_fake_run_fn_raising_timeout_expired():
    """A run_fn raising subprocess.TimeoutExpired is caught by start and classified as timeout."""

    def fake_run(args: list[str], cwd: str | None = None) -> tuple[int, str]:
        raise subprocess.TimeoutExpired(cmd=args, timeout=7200)

    driver = SonnetDriver(run_fn=fake_run)
    result = driver.start("prompt")

    assert result.outcome == "timeout"


@pytest.mark.parametrize(
    ("error", "expected_outcome"),
    [
        ("rate_limit", "quota"),
        ("overloaded", "quota"),
        ("billing_error", "quota"),
        ("account_on_hold", "quota"),
        ("authentication_failed", "auth"),
        ("oauth_org_not_allowed", "auth"),
        ("cloud_credential_error", "auth"),
        ("invalid_request", None),
        ("model_not_found", None),
        ("server_error", None),
        ("max_output_tokens", None),
        ("unknown", None),
    ],
)
def test_classify_retry_error_all_categories(error: str, expected_outcome: str | None):
    """classify_retry_error maps every documented retry category to quota, auth, or None."""
    assert classify_retry_error(error) == expected_outcome


def test_sonnet_outcome_success_with_nonzero_exit_gives_failed():
    """A result message with subtype success but a nonzero exit code is classified as failed."""
    output = (FIXTURES_DIR / "success.jsonl").read_text(encoding="utf-8")
    driver = SonnetDriver(run_fn=lambda args, cwd=None: (1, output))
    result = driver.start("prompt")

    assert result.outcome == "failed"
    assert result.success is False
