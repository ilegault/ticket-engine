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


@dataclass(frozen=True)
class LocalWorkerConfig:
    repos: list[LocalRepoEntry] = field(default_factory=list)
    worktree_base: str = ""   # empty → sibling directory named "worktrees"
    github_token: str = ""    # falls back to PIPELINE_TOKEN env var
    print_timeout: str = "7200"
    quota_error_patterns: list[str] = field(
        default_factory=lambda: ["quota", "rate limit", "exhausted"]
    )
    auth_error_patterns: list[str] = field(
        default_factory=lambda: ["auth", "login", "credential"]
    )


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
    )
