"""Target repository engine configuration.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Config) and Ticket 06 Acceptance Criterion 4
mandate that every target repo has an engine config file defining its default branch,
daily cap, concurrency (default 2), Jules reserve (default 10 of 100), test command,
and paths.
Every tunable has one home: the configuration, never hardcoded literals in logic.
"""
from __future__ import annotations

import logging
import pathlib
import tomllib
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_FILENAMES = (
    ".ticket-engine.toml",
    "ticket-engine.toml",
    ".engine.toml",
)


@dataclass(frozen=True)
class RepoConfig:
    default_branch: str = "master"
    daily_cap: int = 10
    concurrency: int = 2
    jules_reserve: int = 10
    jules_limit: int = 100
    test_command: str = "pytest"
    gate_commands: list[str] = field(
        default_factory=lambda: [
            "ruff check .",
            "python scripts/check_tests_first.py",
            "pytest",
        ]
    )
    source_paths: list[str] = field(default_factory=lambda: ["src"])
    test_paths: list[str] = field(default_factory=lambda: ["tests"])
    ratchet_paths: list[str] = field(default_factory=list)
    stale_claim_hours: int = 12
    max_fix_attempts: int = 3
    circuit_breaker_escalations_limit: int = 2
    circuit_breaker_window_hours: int = 24


def load_repo_config(repo_path: pathlib.Path | str | None = None) -> RepoConfig:
    """Load RepoConfig from target repo root.

    Looks for .ticket-engine.toml, ticket-engine.toml, .engine.toml,
    or [tool.ticket-engine] in pyproject.toml.
    Falls back to defaults if not found.
    """
    base_dir = pathlib.Path(repo_path) if repo_path else pathlib.Path.cwd()
    if base_dir.is_file():
        base_dir = base_dir.parent

    config_data: dict[str, Any] = {}

    for filename in _DEFAULT_CONFIG_FILENAMES:
        candidate = base_dir / filename
        if candidate.is_file():
            try:
                with candidate.open("rb") as f:
                    config_data = tomllib.load(f)
                break
            except (OSError, tomllib.TOMLDecodeError) as exc:
                logger.warning("Failed to parse config file %s: %s", candidate, exc)

    if not config_data:
        pyproject_path = base_dir / "pyproject.toml"
        if pyproject_path.is_file():
            try:
                with pyproject_path.open("rb") as f:
                    pyproj = tomllib.load(f)
                config_data = pyproj.get("tool", {}).get("ticket-engine", {})
            except (OSError, tomllib.TOMLDecodeError) as exc:
                logger.warning("Failed to parse pyproject.toml for ticket-engine config: %s", exc)

    return RepoConfig(
        default_branch=str(config_data.get("default_branch", "master")),
        daily_cap=int(config_data.get("daily_cap", 10)),
        concurrency=int(config_data.get("concurrency", 2)),
        jules_reserve=int(config_data.get("jules_reserve", 10)),
        jules_limit=int(config_data.get("jules_limit", 100)),
        test_command=str(config_data.get("test_command", "pytest")),
        gate_commands=list(
            config_data.get(
                "gate_commands",
                ["ruff check .", "python scripts/check_tests_first.py", "pytest"],
            )
        ),
        source_paths=list(config_data.get("source_paths", ["src"])),
        test_paths=list(config_data.get("test_paths", ["tests"])),
        ratchet_paths=list(config_data.get("ratchet_paths", [])),
        stale_claim_hours=int(config_data.get("stale_claim_hours", 12)),
        max_fix_attempts=int(config_data.get("max_fix_attempts", 3)),
        circuit_breaker_escalations_limit=int(
            config_data.get("circuit_breaker_escalations_limit", 2)
        ),
        circuit_breaker_window_hours=int(
            config_data.get("circuit_breaker_window_hours", 24)
        ),
    )
