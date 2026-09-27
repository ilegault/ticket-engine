"""Claude Sonnet (`claude -p`) driver adapter.

WHY THIS EXISTS
---------------
Ticket 39 (spec `.scratch/sonnet-quota-fallback/spec.md`, §`sonnet.py` adapter)
gives the box a second implementer to fall back to when `agy` reports a quota
error, so the box keeps working through agy's five-hour and weekly quota
windows instead of pausing. Sonnet is driven headlessly the same way `agy` is
in `agy.py`: `SonnetDriver.start` mirrors `AgyDriver.start`'s shape and outcome
vocabulary (`success`, `quota`, `auth`, `timeout`, `failed`) so `LocalWorker`
can treat either driver's result identically.

`--permission-mode bypassPermissions --permission-prompts none` (no `--bare`,
so the run keeps the box's own Claude subscription login rather than requiring
a metered `ANTHROPIC_API_KEY`) guarantees the run always concludes rather than
stopping to ask, so `waiting` is never produced here the way it is for `agy`.

Classification reads Claude Code's own documented `stream-json` events — the
final `result` message and any `system`/`api_retry` event's `error`
category — rather than a guessed text pattern, since `--output-format
stream-json` emits one JSON object per line for exactly this purpose.
"""
from __future__ import annotations

import datetime
import json
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial

logger = logging.getLogger(__name__)

RunFn = Callable[[list[str], str | None], tuple[int, str]]

_QUOTA_RETRY_ERRORS = frozenset(
    ["rate_limit", "overloaded", "billing_error", "account_on_hold"]
)
_AUTH_RETRY_ERRORS = frozenset(
    ["authentication_failed", "oauth_org_not_allowed", "cloud_credential_error"]
)


@dataclass(frozen=True)
class SonnetResult:
    outcome: str
    success: bool = False
    quota_error: bool = False
    reset_at: datetime.datetime | None = None
    session_id: str | None = None
    raw: dict = field(default_factory=dict)
    cost_usd: float | None = None


def classify_retry_error(error: str) -> str | None:
    """Map a `system`/`api_retry` event's `error` category to an outcome.

    Returns "quota", "auth", or None when the category is neither (in which
    case the run classifies as "failed").
    """
    if error in _QUOTA_RETRY_ERRORS:
        return "quota"
    if error in _AUTH_RETRY_ERRORS:
        return "auth"
    return None


def _default_run(
    args: list[str], cwd: str | None = None, timeout: int = 7200
) -> tuple[int, str]:
    result = subprocess.run(
        args, capture_output=True, text=True, cwd=cwd, timeout=timeout, check=False
    )
    return result.returncode, result.stdout or result.stderr


class SonnetDriver:
    """Thin adapter for `claude -p`, driven under the box's own subscription login.

    The run_fn parameter allows tests to inject fake subprocess behaviour
    without spawning a real `claude` process.
    """

    def __init__(
        self,
        run_fn: RunFn | None = None,
        timeout_seconds: int = 7200,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self._run: RunFn = (
            run_fn if run_fn is not None else partial(_default_run, timeout=timeout_seconds)
        )

    def start(self, prompt: str, cwd: str | None = None) -> SonnetResult:
        """Invoke: claude -p <prompt> --output-format stream-json --permission-mode bypassPermissions --permission-prompts none

        Returns a SonnetResult classifying the outcome. The cwd is the
        worktree directory where the Sonnet session should operate.
        """
        args = [
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
        try:
            returncode, output = self._run(args, cwd)
        except subprocess.TimeoutExpired:
            return SonnetResult(outcome="timeout")
        return _parse_sonnet_output(returncode, output)


def _parse_sonnet_output(returncode: int, output: str) -> SonnetResult:
    events: list[dict] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue

    result_event = next((e for e in events if e.get("type") == "result"), None)
    retry_events = [
        e for e in events if e.get("type") == "system" and e.get("subtype") == "api_retry"
    ]

    if result_event is not None and result_event.get("subtype") == "success" and returncode == 0:
        outcome = "success"
    else:
        outcome = "failed"
        for retry_event in retry_events:
            classified = classify_retry_error(str(retry_event.get("error", "")))
            if classified is not None:
                outcome = classified
                break

    session_id = result_event.get("session_id") if result_event else None
    cost_usd = result_event.get("total_cost_usd") if result_event else None
    raw = result_event if result_event is not None else (events[-1] if events else {"raw_output": output})

    return SonnetResult(
        outcome=outcome,
        success=(outcome == "success"),
        quota_error=(outcome == "quota"),
        reset_at=None,
        session_id=str(session_id) if session_id else None,
        raw=raw,
        cost_usd=cost_usd,
    )
