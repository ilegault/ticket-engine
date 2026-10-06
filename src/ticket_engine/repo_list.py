"""Parser, fetcher, and report helpers for engine-repos.toml.

WHY THIS EXISTS
---------------
ADR 0009 rule 4: engine-repos.toml is the single repo list and the single on-switch.
Its entries are [[repos]] tables with repo and box (default true). The morning report,
the box, and the dispatcher all read it. Merging the PR that adds an entry is what
turns a repo on.

This module provides:
1. RepoListEntry and parse_repo_list() to parse the TOML format with strict validation.
2. fetch_repo_list() to fetch and parse the file from GitHub via GitHubClient.
3. repo_names_for_report() to extract all target repos in file order for the morning report.
"""
from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ticket_engine.github import GitHubClient

_REPO_NAME_RE = re.compile(r"^[\w.-]+/[\w.-]+$")


class RepoListError(ValueError):
    """Raised when engine-repos.toml cannot be parsed or validated."""


@dataclass(frozen=True)
class RepoListEntry:
    """An entry in the engine repo list."""

    repo: str
    box: bool = True


def parse_repo_list(text: str) -> list[RepoListEntry]:
    """Parse engine-repos.toml text into a list of RepoListEntry objects, preserving file order.

    Raises:
        RepoListError: If the TOML is invalid, uses the old format (list of strings),
            has invalid repo identifiers, non-boolean box values, or duplicate repos.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        msg = f"Invalid TOML: {exc}"
        raise RepoListError(msg) from exc

    if "repos" not in data:
        return []

    repos_raw = data["repos"]
    if not isinstance(repos_raw, list):
        msg = f"Expected 'repos' to be a list, got {type(repos_raw).__name__}"
        raise RepoListError(msg)

    # Detect old format (repos = ["owner/repo", ...])
    if any(isinstance(item, str) for item in repos_raw):
        msg = "The 'repos' key uses the old format (list of strings). Use [[repos]] tables instead."
        raise RepoListError(msg)

    entries: list[RepoListEntry] = []
    seen: set[str] = set()

    for item in repos_raw:
        if not isinstance(item, dict):
            msg = f"Expected repo entry table, got {type(item).__name__}"
            raise RepoListError(msg)

        if "repo" not in item or not isinstance(item["repo"], str):
            msg = f"Repo entry {item} is missing required string 'repo' key"
            raise RepoListError(msg)

        repo_name = item["repo"]
        if not _REPO_NAME_RE.match(repo_name):
            msg = f"Invalid repo identifier '{repo_name}'. Must match 'owner/name'."
            raise RepoListError(msg)

        if "box" in item:
            if not isinstance(item["box"], bool):
                msg = f"Repo entry '{repo_name}' has non-boolean 'box' value: {item['box']!r}"
                raise RepoListError(msg)
            box = item["box"]
        else:
            box = True

        key = repo_name.lower()
        if key in seen:
            msg = f"Duplicate repo identifier '{repo_name}' (case-insensitive duplicate)"
            raise RepoListError(msg)
        seen.add(key)

        entries.append(RepoListEntry(repo=repo_name, box=box))

    return entries


def fetch_repo_list(client: GitHubClient, engine_repo: str) -> list[RepoListEntry]:
    """Fetch engine-repos.toml from engine_repo's default branch and return parsed entries.

    Calls client.get_file_contents(engine_repo, 'engine-repos.toml') with no ref.
    Errors propagate.
    """
    data = client.get_file_contents(engine_repo, "engine-repos.toml")
    content = data.get("content", "")
    return parse_repo_list(content)


def repo_names_for_report(text: str) -> list[str]:
    """Extract repo names of every entry in file order, regardless of box setting."""
    return [entry.repo for entry in parse_repo_list(text)]
