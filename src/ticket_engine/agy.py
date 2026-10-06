"""Antigravity CLI (agy) adapter.

WHY THIS EXISTS
---------------
Ticket 09 and Ticket 19 require driving the Antigravity CLI to implement tickets.
The spec mandates all I/O lives in thin adapters so cores remain pure and
tests can use fakes.

In headless mode, agy soft-denies unapproved tools and still exits 0, which would
cause tools to silently fail unless --dangerously-skip-permissions is passed (ADR 0007).
The default agy print timeout is 5 minutes, which cuts off long-running ticket sessions,
so --print-timeout is passed with a configurable duration (a duration with a unit, e.g. "2h"; agy rejects a bare number of seconds).

There is no documented quota-status endpoint or API for agy, so the previous pre-flight
quota check and reserve are removed. Sessions are driven via:
  agy -p <prompt> --output-format json --dangerously-skip-permissions --print-timeout <value>
and their outputs are classified into exact outcomes (success, quota, auth, timeout,
waiting, failed). This logger reaches only the box's local rotating file and the local
work-windows console; AgyDriver never runs in Actions, so ADR 0007 rule 4 holds.
"""
from __future__ import annotations

import datetime
import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

RunFn = Callable[[list[str], str | None], tuple[int, str]]

_DEFAULT_QUOTA_ERROR_PATTERNS = ["quota", "rate limit", "exhausted"]
_DEFAULT_AUTH_ERROR_PATTERNS = ["auth", "login", "credential"]


@dataclass(frozen=True)
class AgyResult:
    outcome: str
    success: bool = False
    quota_error: bool = False
    reset_at: datetime.datetime | None = None
    session_id: str | None = None
    raw: dict = field(default_factory=dict)


def _default_run(args: list[str], cwd: str | None = None) -> tuple[int, str]:
    import subprocess
    result = subprocess.run(args, capture_output=True, text=True, cwd=cwd, check=False)
    return result.returncode, result.stdout or result.stderr


class AgyDriver:
    """Thin adapter for the Antigravity CLI.

    The run_fn parameter allows tests to inject fake subprocess behaviour
    without spawning real processes.
    """

    def __init__(
        self,
        run_fn: RunFn | None = None,
        print_timeout: str = "2h",
        quota_error_patterns: Sequence[str] | None = None,
        auth_error_patterns: Sequence[str] | None = None,
    ) -> None:
        self._run: RunFn = run_fn if run_fn is not None else _default_run
        self.print_timeout = str(print_timeout)
        self.quota_error_patterns = (
            list(quota_error_patterns)
            if quota_error_patterns is not None
            else list(_DEFAULT_QUOTA_ERROR_PATTERNS)
        )
        self.auth_error_patterns = (
            list(auth_error_patterns)
            if auth_error_patterns is not None
            else list(_DEFAULT_AUTH_ERROR_PATTERNS)
        )

    def start(self, prompt: str, cwd: str | None = None) -> AgyResult:
        """Invoke: agy -p <prompt> --output-format json --dangerously-skip-permissions --print-timeout <value>

        Returns an AgyResult classifying the outcome.
        The cwd is the worktree directory where agy should operate.
        """
        args = [
            "agy",
            "-p",
            prompt,
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
            "--print-timeout",
            self.print_timeout,
        ]
        returncode, output = self._run(args, cwd)
        result = _parse_agy_output(
            returncode,
            output,
            quota_error_patterns=self.quota_error_patterns,
            auth_error_patterns=self.auth_error_patterns,
        )
        if result.outcome == "success":
            logger.info("agy finished: outcome success, exit %d", returncode)
        else:
            logger.warning(
                "agy finished: outcome %s, exit %d, output: %s",
                result.outcome,
                returncode,
                output.strip()[-4000:],
            )
        return result


def _parse_agy_output(
    returncode: int,
    output: str,
    quota_error_patterns: Sequence[str],
    auth_error_patterns: Sequence[str],
) -> AgyResult:
    raw: dict = {}
    try:
        raw = json.loads(output.strip()) if output.strip() else {}
    except (json.JSONDecodeError, ValueError):
        raw = {"raw_output": output}

    status = str(raw.get("status", "")).upper()

    if status == "SUCCESS" and returncode == 0:
        outcome = "success"
    elif status == "WAITING":
        outcome = "waiting"
    elif status in ("CANCELED", "INTERRUPTED"):
        outcome = "timeout"
    elif status == "ERROR":
        msg = str(raw.get("message", ""))
        err = str(raw.get("error", ""))
        combined_text = f"{msg} {err} {output}".lower()
        if any(p.lower() in combined_text for p in quota_error_patterns):
            outcome = "quota"
        elif any(p.lower() in combined_text for p in auth_error_patterns):
            outcome = "auth"
        else:
            outcome = "failed"
    else:
        outcome = "failed"

    success = (outcome == "success")
    quota_error = (outcome == "quota")
    reset_at = _parse_dt(raw.get("reset_at"))
    session_id = raw.get("session_id") or raw.get("conversation_id") or raw.get("id")

    return AgyResult(
        outcome=outcome,
        success=success,
        quota_error=quota_error,
        reset_at=reset_at,
        session_id=str(session_id) if session_id else None,
        raw=raw,
    )


def _parse_dt(value: object) -> datetime.datetime | None:
    if not value:
        return None
    try:
        return datetime.datetime.fromisoformat(str(value))
    except (ValueError, AttributeError):
        return None
