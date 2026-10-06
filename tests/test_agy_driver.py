"""Tests for AgyDriver argument list and outcome classification.

WHY THIS EXISTS
---------------
Ticket 19 and Spec §agy adapter mandate that:
1. Every agy invocation is:
   agy -p <prompt> --output-format json --dangerously-skip-permissions --print-timeout <value>
2. AgyResult classifies outcomes into:
   success, quota, auth, timeout, waiting, failed
3. Pre-flight quota check and --continue are removed (no documented sources).
"""
from __future__ import annotations

import logging
import pathlib

import pytest

from ticket_engine.agy import AgyDriver
from ticket_engine.local_config import LocalWorkerConfig

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "agy"


def test_agy_driver_start_exact_argument_list():
    """AgyDriver.start calls run_fn with exact flags and prompt as a single argument."""
    seen_calls: list[tuple[list[str], str | None]] = []

    def fake_run(args: list[str], cwd: str | None = None) -> tuple[int, str]:
        seen_calls.append((list(args), cwd))
        return 0, '{"status": "SUCCESS"}'

    config = LocalWorkerConfig()
    driver = AgyDriver(run_fn=fake_run, print_timeout=config.print_timeout)
    prompt = "Implement ticket 19 with all acceptance criteria."
    result = driver.start(prompt, cwd="/worktree/path")

    assert len(seen_calls) == 1
    args, cwd = seen_calls[0]
    expected_args = [
        "agy",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
        "--print-timeout",
        config.print_timeout,
    ]
    assert args == expected_args
    assert cwd == "/worktree/path"
    assert result.outcome == "success"
    assert result.success is True


def test_agy_driver_start_custom_print_timeout():
    """AgyDriver uses custom print_timeout from config when provided."""
    seen_calls = []

    def fake_run(args: list[str], cwd: str | None = None) -> tuple[int, str]:
        seen_calls.append(list(args))
        return 0, '{"status": "SUCCESS"}'

    driver = AgyDriver(run_fn=fake_run, print_timeout="3600")
    driver.start("do something")

    assert seen_calls[0] == [
        "agy",
        "-p",
        "do something",
        "--output-format",
        "json",
        "--dangerously-skip-permissions",
        "--print-timeout",
        "3600",
    ]


@pytest.mark.parametrize(
    ("fixture_name_or_raw", "exit_code", "expected_outcome"),
    [
        ("success.json", 0, "success"),
        ("waiting.json", 0, "waiting"),
        ("interrupted.json", 1, "timeout"),
        ("canceled.json", 1, "timeout"),
        ("quota.json", 1, "quota"),
        ("auth.json", 1, "auth"),
        ("error.json", 1, "failed"),
        ("non-json output string: fatal error", 1, "failed"),
    ],
)
def test_agy_outcome_classification_all_eight_cases(
    fixture_name_or_raw: str,
    exit_code: int,
    expected_outcome: str,
):
    """AgyResult classifies outputs into exactly one outcome across all eight cases."""
    if fixture_name_or_raw.endswith(".json"):
        output = (FIXTURES_DIR / fixture_name_or_raw).read_text(encoding="utf-8")
    else:
        output = fixture_name_or_raw

    driver = AgyDriver(run_fn=lambda args, cwd=None: (exit_code, output))
    result = driver.start("prompt")

    assert result.outcome == expected_outcome


def test_agy_outcome_success_with_nonzero_exit_gives_failed():
    """status SUCCESS with exit code != 0 is classified as failed."""
    output = (FIXTURES_DIR / "success.json").read_text(encoding="utf-8")
    driver = AgyDriver(run_fn=lambda args, cwd=None: (1, output))
    result = driver.start("prompt")
    assert result.outcome == "failed"
    assert result.success is False


def test_agy_outcome_custom_patterns_passed_into_driver():
    """Patterns are passed into AgyDriver, never hardcoded literals."""
    quota_output = '{"status": "ERROR", "message": "out of tokens for today"}'
    auth_output = '{"status": "ERROR", "message": "forbidden by policy"}'

    # With default patterns, these would be 'failed'
    default_driver = AgyDriver(run_fn=lambda a, cwd=None: (1, quota_output))
    assert default_driver.start("prompt").outcome == "failed"

    # With custom patterns passed into driver, they are classified accordingly
    custom_driver_quota = AgyDriver(
        run_fn=lambda a, cwd=None: (1, quota_output),
        quota_error_patterns=["out of tokens"],
    )
    assert custom_driver_quota.start("prompt").outcome == "quota"

    custom_driver_auth = AgyDriver(
        run_fn=lambda a, cwd=None: (1, auth_output),
        auth_error_patterns=["forbidden"],
    )
    assert custom_driver_auth.start("prompt").outcome == "auth"


def test_removed_symbols_not_stubbed():
    """Removed methods and config attributes no longer exist."""
    assert not hasattr(AgyDriver, "continue_session")
    assert not hasattr(AgyDriver, "read_quota")
    assert not hasattr(LocalWorkerConfig, "agy_quota_url")
    assert not hasattr(LocalWorkerConfig, "agy_quota_reserve_pct")


def test_agy_driver_parses_recorded_real_success_output():
    fixture_text = (FIXTURES_DIR / "success_recorded.json").read_text(encoding="utf-8")
    driver = AgyDriver(run_fn=lambda args, cwd=None: (0, fixture_text))
    result = driver.start("prompt")

    assert result.outcome == "success"
    assert result.success is True
    assert result.session_id == "0614ac83-77db-445a-beb6-c0221aaa2a2f"


def test_agy_driver_logs_failed_output_but_never_the_prompt(caplog):
    driver = AgyDriver(
        run_fn=lambda args, cwd=None: (1, 'Error: invalid value "7200" for flag --print-timeout')
    )
    prompt = "PROMPT-MARKER-do-not-log"

    with caplog.at_level(logging.WARNING):
        result = driver.start(prompt)

    assert result.outcome == "failed"
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 1
    msg = warning_records[0].getMessage()
    assert "outcome failed" in msg
    assert "exit 1" in msg
    assert 'invalid value "7200"' in msg
    assert "PROMPT-MARKER-do-not-log" not in caplog.text

