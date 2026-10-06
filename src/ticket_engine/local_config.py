"""Local worker configuration.

WHY THIS EXISTS
---------------
Ticket 09 and Ticket 19 require a local worker command that operates across multiple
developer-maintained clones listed in a local config file. This config
lives outside any repo (it is a developer tool config, not committed)
and records repo paths, their GitHub names, and agy settings including
print_timeout and error patterns.

There is no documented quota source for agy, so the previous pre-flight quota
reserve is removed. Timeout and pattern tunables live here, never hardcoded in logic.

Ticket 31 (spec §Local config) adds the box-worker loop's own tunables:
concurrency, poll/status intervals, the weekly-cap timeline, the engine repo
the box posts its status and alerts to, and the local folder its ledger,
quota-pause record and rotating logs live in. Every one of these is a home
for a literal that used to have none (AGENTS.md §3 invariant 6).
"""
from __future__ import annotations

import logging
import pathlib
import tomllib
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = pathlib.Path.home() / ".ticket-engine-local.toml"


@dataclass(frozen=True)
class LocalRepoEntry:
    path: str  # absolute path to local git clone
    repo: str  # "owner/repo" on GitHub


def _default_logs_dir() -> str:
    return str(pathlib.Path.home() / "ticket-engine-box" / "logs")


@dataclass(frozen=True)
class LocalWorkerConfig:
    repos: list[LocalRepoEntry] = field(default_factory=list)
    worktree_base: str = ""   # empty → sibling directory named "worktrees"
    github_token: str = ""    # falls back to PIPELINE_TOKEN env var
    print_timeout: str = "7200"
    checkpoint_push_minutes: int = 20
    max_resumes_per_ticket: int = 3
    quota_error_patterns: list[str] = field(
        default_factory=lambda: ["quota", "rate limit", "exhausted"]
    )
    auth_error_patterns: list[str] = field(
        default_factory=lambda: ["auth", "login", "credential"]
    )
    concurrency: int = 1
    poll_interval_minutes: int = 10
    status_interval_minutes: int = 30
    weekly_cap_after_hours: int = 5
    weekly_cap_backoff_hours: int = 12
    engine_repo: str = "ilegault/ticket-engine"
    logs_dir: str = field(default_factory=_default_logs_dir)
    sonnet_enabled: bool = False
    sonnet_timeout_seconds: int = 7200
    # Upper bound on any one git subprocess. A git call waiting on a
    # credential prompt in the box's windowless scheduled task would
    # otherwise hang the loop forever.
    git_timeout_seconds: int = 300


def load_local_config(path: pathlib.Path | str | None = None) -> LocalWorkerConfig:
    """Load local worker config from a TOML file.

    Looks at the given path, or ~/.ticket-engine-local.toml by default.
    Returns defaults if the file does not exist or cannot be parsed.

    Expected TOML format::

        github_token = "ghp_..."   # optional; falls back to PIPELINE_TOKEN

        [agy]
        print_timeout = "7200"
        quota_error_patterns = ["quota", "rate limit", "exhausted"]
        auth_error_patterns = ["auth", "login", "credential"]

        [[repos]]
        path = "/home/dev/projects/myrepo"
        repo  = "owner/myrepo"
    """
    config_path = pathlib.Path(path) if path is not None else _DEFAULT_CONFIG_PATH

    if not config_path.is_file():
        return LocalWorkerConfig()

    try:
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        logger.warning("Failed to parse local config %s: %s", config_path, exc)
        return LocalWorkerConfig()

    repos = [
        LocalRepoEntry(path=str(r.get("path", "")), repo=str(r.get("repo", "")))
        for r in data.get("repos", [])
    ]
    agy = data.get("agy", {})
    quota_patterns = agy.get("quota_error_patterns")
    auth_patterns = agy.get("auth_error_patterns")
    return LocalWorkerConfig(
        repos=repos,
        worktree_base=str(data.get("worktree_base", "")),
        github_token=str(data.get("github_token", "")),
        print_timeout=str(agy.get("print_timeout", "7200")),
        checkpoint_push_minutes=int(data.get("checkpoint_push_minutes", 20)),
        max_resumes_per_ticket=int(data.get("max_resumes_per_ticket", 3)),
        quota_error_patterns=(
            [str(p) for p in quota_patterns]
            if quota_patterns is not None
            else ["quota", "rate limit", "exhausted"]
        ),
        auth_error_patterns=(
            [str(p) for p in auth_patterns]
            if auth_patterns is not None
            else ["auth", "login", "credential"]
        ),
        concurrency=int(data.get("concurrency", 1)),
        poll_interval_minutes=int(data.get("poll_interval_minutes", 10)),
        status_interval_minutes=int(data.get("status_interval_minutes", 30)),
        weekly_cap_after_hours=int(data.get("weekly_cap_after_hours", 5)),
        weekly_cap_backoff_hours=int(data.get("weekly_cap_backoff_hours", 12)),
        engine_repo=str(data.get("engine_repo", "ilegault/ticket-engine")),
        logs_dir=str(data.get("logs_dir", "")) or _default_logs_dir(),
        sonnet_enabled=bool(data.get("sonnet_enabled", False)),
        sonnet_timeout_seconds=int(data.get("sonnet_timeout_seconds", 7200)),
        git_timeout_seconds=int(data.get("git_timeout_seconds", 300)),
    )
