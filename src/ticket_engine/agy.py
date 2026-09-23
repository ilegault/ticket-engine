"""Antigravity CLI (agy) adapter.

WHY THIS EXISTS
---------------
Ticket 09 requires driving the Antigravity CLI to implement windows tickets.
The spec mandates all I/O lives in thin adapters so cores remain pure and
tests can use fakes. This module wraps:
  - agy -p <prompt> --output-format json     (start a session)
  - agy --continue --output-format json      (resume after quota pause)
  - HTTP GET to the local quota-status endpoint

All three are injectable via run_fn / fetch_fn so tests never spawn real
subprocesses or make network calls. This mirrors the pattern used for the
Jules and GitHub adapters in tickets 06 and 07.
"""
from __future__ import annotations

import datetime
import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

RunFn = Callable[[list[str], str | None], tuple[int, str]]
FetchFn = Callable[[str], dict | None]


@dataclass(frozen=True)
class QuotaInfo:
    remaining_pct: float                       # 0.0–1.0
    reset_at: datetime.datetime | None = None  # when the rolling window resets


@dataclass(frozen=True)
class AgyResult:
    success: bool
    quota_error: bool = False
    reset_at: datetime.datetime | None = None   # present when quota_error is True
    session_id: str | None = None               # for --continue if reported
    raw: dict = field(default_factory=dict)


def _default_run(args: list[str], cwd: str | None = None) -> tuple[int, str]:
    import subprocess
    result = subprocess.run(args, capture_output=True, text=True, cwd=cwd, check=False)
    return result.returncode, result.stdout or result.stderr


def _default_fetch(url: str) -> dict | None:
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
        return None


class AgyDriver:
    """Thin adapter for the Antigravity CLI.

    The run_fn and fetch_fn parameters allow tests to inject fake subprocess
    and HTTP behaviour without spawning real processes or hitting live endpoints.
    """

    def __init__(
        self,
        run_fn: RunFn | None = None,
        fetch_fn: FetchFn | None = None,
    ) -> None:
        self._run: RunFn = run_fn if run_fn is not None else _default_run
        self._fetch: FetchFn = fetch_fn if fetch_fn is not None else _default_fetch

    def read_quota(self, url: str) -> QuotaInfo | None:
        """Read remaining quota from the local Antigravity status endpoint.

        Returns None when the endpoint is unavailable (connection refused, timeout,
        or parse error). The caller treats None as "proceed and rely on quota-error
        detection" per Ticket 09 AC4.
        """
        data = self._fetch(url)
        if data is None:
            return None
        pct = float(data.get("remaining_pct", 1.0))
        reset_at = _parse_dt(data.get("reset_at"))
        return QuotaInfo(remaining_pct=pct, reset_at=reset_at)

    def start(self, prompt: str, cwd: str | None = None) -> AgyResult:
        """Invoke: agy -p <prompt> --output-format json

        Returns an AgyResult indicating success, quota error, or other failure.
        The cwd is the worktree directory where agy should operate.
        """
        args = ["agy", "-p", prompt, "--output-format", "json"]
        returncode, output = self._run(args, cwd)
        return _parse_agy_output(returncode, output)

    def continue_session(self, cwd: str | None = None) -> AgyResult:
        """Invoke: agy --continue --output-format json

        Resumes the most recent session in the cwd. Used after a quota pause.
        """
        args = ["agy", "--continue", "--output-format", "json"]
        returncode, output = self._run(args, cwd)
        return _parse_agy_output(returncode, output)


def _parse_agy_output(returncode: int, output: str) -> AgyResult:
    raw: dict = {}
    try:
        raw = json.loads(output.strip()) if output.strip() else {}
    except json.JSONDecodeError:
        raw = {"raw_output": output}

    status = str(raw.get("status", "")).lower()
    quota_error = status == "quota_error" or (
        returncode != 0 and "quota" in output.lower() and status != "error"
    )
    success = returncode == 0 and not quota_error

    reset_at = _parse_dt(raw.get("reset_at"))
    session_id = raw.get("session_id") or raw.get("id")

    return AgyResult(
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
