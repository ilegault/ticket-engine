"""Tests for engine-repos.toml parser, fetcher, and report helper.

WHY THIS EXISTS
---------------
Ticket 53 (ADR 0009): engine-repos.toml becomes the single list of target repos
(the repo list) across the engine. It is read by the morning report, the box,
and the dispatcher.
These tests verify that:
1. parse_repo_list parses valid [[repos]] TOML, defaults box=True, keeps file
   order, and rejects invalid input (old format, bad repo syntax, non-bool box,
   duplicates ignoring case).
2. The real engine-repos.toml parses and contains ilegault/slackbot.
3. fetch_repo_list fetches engine-repos.toml from GitHub and parses it.
4. repo_names_for_report extracts all repo names in file order regardless of box.
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock

import pytest

from ticket_engine.github import GitHubClient
from ticket_engine.repo_list import (
    RepoListEntry,
    RepoListError,
    fetch_repo_list,
    parse_repo_list,
    repo_names_for_report,
)


def test_parse_repo_list_reads_entries_in_order_with_box_default():
    toml_text = """
    [[repos]]
    repo = "owner/first"

    [[repos]]
    repo = "owner/second"
    box = false

    [[repos]]
    repo = "other-org/third.repo"
    box = true
    """
    entries = parse_repo_list(toml_text)
    assert entries == [
        RepoListEntry(repo="owner/first", box=True),
        RepoListEntry(repo="owner/second", box=False),
        RepoListEntry(repo="other-org/third.repo", box=True),
    ]


def test_parse_repo_list_empty_when_no_repos_key():
    assert parse_repo_list("") == []
    assert parse_repo_list("# only comments\n") == []
    assert parse_repo_list("[settings]\nfoo = 'bar'") == []


def test_parse_repo_list_rejects_invalid_toml():
    with pytest.raises(RepoListError) as exc_info:
        parse_repo_list("this is not valid toml = [")
    assert exc_info.value


def test_parse_repo_list_rejects_old_format():
    old_toml = """
    repos = [
        "owner/first",
        "owner/second",
    ]
    """
    with pytest.raises(RepoListError) as exc_info:
        parse_repo_list(old_toml)
    assert "old format" in str(exc_info.value).lower()


def test_parse_repo_list_rejects_missing_repo():
    bad_toml = """
    [[repos]]
    box = true
    """
    with pytest.raises(RepoListError) as exc_info:
        parse_repo_list(bad_toml)
    assert "repo" in str(exc_info.value).lower()


@pytest.mark.parametrize(
    "invalid_name",
    [
        "noslash",
        "too/many/slashes",
        "bad name/repo",
        "owner/repo with spaces",
        "owner/",
        "/repo",
    ],
)
def test_parse_repo_list_rejects_invalid_repo_name(invalid_name: str):
    toml_text = f"""
    [[repos]]
    repo = "{invalid_name}"
    """
    with pytest.raises(RepoListError) as exc_info:
        parse_repo_list(toml_text)
    assert "repo" in str(exc_info.value).lower()


@pytest.mark.parametrize("invalid_box", ['"true"', "1", "[true]", "{val = true}"])
def test_parse_repo_list_rejects_non_bool_box(invalid_box: str):
    toml_text = f"""
    [[repos]]
    repo = "owner/repo"
    box = {invalid_box}
    """
    with pytest.raises(RepoListError) as exc_info:
        parse_repo_list(toml_text)
    assert "box" in str(exc_info.value).lower()


def test_parse_repo_list_rejects_duplicate_repo_case_insensitive():
    toml_text = """
    [[repos]]
    repo = "Owner/Repo"

    [[repos]]
    repo = "owner/repo"
    """
    with pytest.raises(RepoListError) as exc_info:
        parse_repo_list(toml_text)
    assert "duplicate" in str(exc_info.value).lower()


def test_engine_repos_toml_parses_and_lists_slackbot():
    root = pathlib.Path(__file__).resolve().parent.parent
    engine_repos_path = root / "engine-repos.toml"
    assert engine_repos_path.is_file()

    entries = parse_repo_list(engine_repos_path.read_text(encoding="utf-8"))
    assert RepoListEntry(repo="ilegault/slackbot", box=True) in entries


def test_fetch_repo_list_calls_client_and_returns_entries():
    client = MagicMock(spec=GitHubClient)
    client.get_file_contents.return_value = {
        "content": '[[repos]]\nrepo = "owner/repo"\nbox = false\n',
        "sha": "abc123sha",
    }

    entries = fetch_repo_list(client, "owner/engine")
    client.get_file_contents.assert_called_once_with("owner/engine", "engine-repos.toml")
    assert entries == [RepoListEntry(repo="owner/repo", box=False)]


def test_fetch_repo_list_propagates_client_error():
    client = MagicMock(spec=GitHubClient)
    client.get_file_contents.side_effect = RuntimeError("network failure")

    with pytest.raises(RuntimeError, match="network failure"):
        fetch_repo_list(client, "owner/engine")


def test_repo_names_for_report_includes_box_false_repos():
    toml_text = """
    [[repos]]
    repo = "owner/first"
    box = true

    [[repos]]
    repo = "owner/second"
    box = false

    [[repos]]
    repo = "owner/third"
    """
    names = repo_names_for_report(toml_text)
    assert names == ["owner/first", "owner/second", "owner/third"]
