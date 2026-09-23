"""Local worker configuration.

WHY THIS EXISTS
---------------
Ticket 09 requires a local worker command that operates across multiple
developer-maintained clones listed in a local config file. This config
lives outside any repo (it is a developer tool config, not committed)
and records repo paths, their GitHub names, and agy settings including
the quota status endpoint and reserve threshold.

The 20% quota reserve is the tunable defined in the spec §Local worker
and Ticket 09 AC4. It must live in config, not hardcoded in logic.
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
    agy_quota_url: str = "http://localhost:8765/status"
    agy_quota_reserve_pct: float = 0.20
    worktree_base: str = ""   # empty → sibling directory named "worktrees"
    github_token: str = ""    # falls back to PIPELINE_TOKEN env var


def load_local_config(path: pathlib.Path | str | None = None) -> LocalWorkerConfig:
    """Load local worker config from a TOML file.

    Looks at the given path, or ~/.ticket-engine-local.toml by default.
    Returns defaults if the file does not exist or cannot be parsed.

    Expected TOML format::

        github_token = "ghp_..."   # optional; falls back to PIPELINE_TOKEN

        [agy]
        quota_url = "http://localhost:8765/status"
        quota_reserve_pct = 0.20

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
    return LocalWorkerConfig(
        repos=repos,
        agy_quota_url=str(agy.get("quota_url", "http://localhost:8765/status")),
        agy_quota_reserve_pct=float(agy.get("quota_reserve_pct", 0.20)),
        worktree_base=str(data.get("worktree_base", "")),
        github_token=str(data.get("github_token", "")),
    )
