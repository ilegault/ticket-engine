"""Tests for the integrity gate runner adapter.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Adapters) and Ticket 03 Acceptance Criterion 5
require running the integrity gate against a git repository / PR environment,
posting PR comments and check statuses, and writing to GITHUB_STEP_SUMMARY.
This test suite verifies the runner adapter with faked git operations and faked GitHub API.
"""
from __future__ import annotations

import pathlib
from unittest.mock import MagicMock, patch

from ticket_engine.integrity import IntegrityVerdict, Verdict
from ticket_engine.integrity_runner import format_verdict_comment, run_integrity_gate


def test_format_verdict_comment_includes_verdict_and_reasons():
    verdict = IntegrityVerdict(
        verdict=Verdict.PASS,
        reasons=["All integrity checks passed (checks 1, 2, 6)"],
    )
    comment = format_verdict_comment(verdict)
    assert "Integrity Gate" in comment
    assert "`pass`" in comment
    assert "All integrity checks passed" in comment


def test_format_verdict_comment_does_not_contain_secrets():
    verdict = IntegrityVerdict(
        verdict=Verdict.FAIL,
        reasons=["Check 1 fail: Test 'test_auth' in tests/test_auth.py is newly skipped"],
    )
    comment = format_verdict_comment(verdict)
    assert "ghp_super_secret_token" not in comment
    assert "Check 1 fail" in comment


def test_run_integrity_gate_pass_posts_comment_and_status(tmp_path: pathlib.Path):
    mock_client = MagicMock()

    with patch("ticket_engine.integrity_runner.GitHubClient", return_value=mock_client), \
         patch("ticket_engine.integrity_runner.get_base_tree_from_git", return_value={"tests/test_a.py": "def test_a(): assert True\n"}), \
         patch("ticket_engine.integrity_runner.get_pr_diff_from_git", return_value=""), \
         patch("ticket_engine.integrity_runner.find_ticket_content_from_git", return_value="# 01: T\n**Status:** done\n## Acceptance criteria\n- [x] Done\n"):

        summary_file = tmp_path / "step_summary.md"
        exit_code = run_integrity_gate(
            repo_path=tmp_path,
            base_ref="origin/master",
            token="fake-token",
            repo_name="owner/repo",
            pr_number=99,
            head_sha="1234567890abcdef",
            step_summary_path=summary_file,
        )

        assert exit_code == 0
        mock_client.post_or_update_pr_comment.assert_called_once()
        mock_client.set_commit_status.assert_called_once()
        status_call = mock_client.set_commit_status.call_args[1]
        assert status_call["state"] == "success"

        # Check step summary was written
        assert summary_file.is_file()
        assert "Integrity Gate" in summary_file.read_text(encoding="utf-8")


def test_run_integrity_gate_fail_returns_1_and_sets_failure_status(tmp_path: pathlib.Path):
    mock_client = MagicMock()

    with patch("ticket_engine.integrity_runner.GitHubClient", return_value=mock_client), \
         patch("ticket_engine.integrity_runner.get_base_tree_from_git", return_value={"tests/test_a.py": "def test_a(): assert True\n"}), \
         patch("ticket_engine.integrity_runner.get_pr_diff_from_git", return_value=""), \
         patch("ticket_engine.integrity_runner.find_ticket_content_from_git", return_value="# 01: T\n**Status:** in-progress\n## Acceptance criteria\n- [x] Done\n"):

        exit_code = run_integrity_gate(
            repo_path=tmp_path,
            base_ref="origin/master",
            token="fake-token",
            repo_name="owner/repo",
            pr_number=99,
            head_sha="1234567890abcdef",
        )

        assert exit_code == 1
        status_call = mock_client.set_commit_status.call_args[1]
        assert status_call["state"] == "failure"


def test_run_integrity_gate_hold_returns_0_and_sets_pending_status(tmp_path: pathlib.Path):
    mock_client = MagicMock()

    with patch("ticket_engine.integrity_runner.GitHubClient", return_value=mock_client), \
         patch("ticket_engine.integrity_runner.get_base_tree_from_git", return_value={"tests/test_a.py": "def test_a(): assert True\n"}), \
         patch("ticket_engine.integrity_runner.get_pr_diff_from_git", return_value=""), \
         patch("ticket_engine.integrity_runner.find_ticket_content_from_git", return_value="# 01: T\n**Status:** done\n**Auto-merge:** no\n## Acceptance criteria\n- [x] Done\n"):

        exit_code = run_integrity_gate(
            repo_path=tmp_path,
            base_ref="origin/master",
            token="fake-token",
            repo_name="owner/repo",
            pr_number=99,
            head_sha="1234567890abcdef",
        )

        assert exit_code == 0
        status_call = mock_client.set_commit_status.call_args[1]
        assert status_call["state"] == "pending"


def test_run_integrity_gate_pass_merges_pr(tmp_path: pathlib.Path):
    mock_client = MagicMock()

    with patch("ticket_engine.integrity_runner.GitHubClient", return_value=mock_client), \
         patch("ticket_engine.integrity_runner.get_base_tree_from_git", return_value={"tests/test_a.py": "def test_a(): assert True\n"}), \
         patch("ticket_engine.integrity_runner.get_pr_diff_from_git", return_value=""), \
         patch("ticket_engine.integrity_runner.find_ticket_content_from_git", return_value="# 01: T\n**Status:** done\n## Acceptance criteria\n- [x] Done\n"):

        exit_code = run_integrity_gate(
            repo_path=tmp_path,
            base_ref="origin/master",
            token="fake-token",
            repo_name="owner/repo",
            pr_number=99,
            head_sha="1234567890abcdef",
        )

        assert exit_code == 0
        # The gate is itself a required check and is still running here, so an
        # immediate merge is always refused. It hands the merge to GitHub instead,
        # which merges once every required check (CI included) is green.
        mock_client.enable_auto_merge.assert_called_once_with(
            repo="owner/repo", pr_number=99, merge_method="merge",
        )
        mock_client.merge_pull_request.assert_not_called()


def test_run_integrity_gate_pass_falls_back_to_direct_merge_when_auto_merge_refused(
    tmp_path: pathlib.Path,
):
    # e.g. every check already finished, so GitHub won't queue auto-merge.
    mock_client = MagicMock()
    mock_client.enable_auto_merge.return_value = False

    with patch("ticket_engine.integrity_runner.GitHubClient", return_value=mock_client), \
         patch("ticket_engine.integrity_runner.get_base_tree_from_git", return_value={"tests/test_a.py": "def test_a(): assert True\n"}), \
         patch("ticket_engine.integrity_runner.get_pr_diff_from_git", return_value=""), \
         patch("ticket_engine.integrity_runner.find_ticket_content_from_git", return_value="# 01: T\n**Status:** done\n## Acceptance criteria\n- [x] Done\n"):

        run_integrity_gate(
            repo_path=tmp_path,
            base_ref="origin/master",
            token="fake-token",
            repo_name="owner/repo",
            pr_number=99,
            head_sha="1234567890abcdef",
        )

        mock_client.merge_pull_request.assert_called_once_with(
            repo="owner/repo", pr_number=99, merge_method="merge",
        )


def test_run_integrity_gate_fail_does_not_merge_pr(tmp_path: pathlib.Path):
    mock_client = MagicMock()

    with patch("ticket_engine.integrity_runner.GitHubClient", return_value=mock_client), \
         patch("ticket_engine.integrity_runner.get_base_tree_from_git", return_value={"tests/test_a.py": "def test_a(): assert True\n"}), \
         patch("ticket_engine.integrity_runner.get_pr_diff_from_git", return_value=""), \
         patch("ticket_engine.integrity_runner.find_ticket_content_from_git", return_value="# 01: T\n**Status:** in-progress\n## Acceptance criteria\n- [x] Done\n"):

        run_integrity_gate(
            repo_path=tmp_path,
            base_ref="origin/master",
            token="fake-token",
            repo_name="owner/repo",
            pr_number=99,
            head_sha="1234567890abcdef",
        )

        mock_client.merge_pull_request.assert_not_called()
        mock_client.enable_auto_merge.assert_not_called()
        # A new push that fails must cancel auto-merge queued by an earlier pass.
        mock_client.disable_auto_merge.assert_called_once_with(repo="owner/repo", pr_number=99)


def test_run_integrity_gate_hold_does_not_merge_pr(tmp_path: pathlib.Path):
    mock_client = MagicMock()

    with patch("ticket_engine.integrity_runner.GitHubClient", return_value=mock_client), \
         patch("ticket_engine.integrity_runner.get_base_tree_from_git", return_value={"tests/test_a.py": "def test_a(): assert True\n"}), \
         patch("ticket_engine.integrity_runner.get_pr_diff_from_git", return_value=""), \
         patch("ticket_engine.integrity_runner.find_ticket_content_from_git", return_value="# 01: T\n**Status:** done\n**Auto-merge:** no\n## Acceptance criteria\n- [x] Done\n"):

        run_integrity_gate(
            repo_path=tmp_path,
            base_ref="origin/master",
            token="fake-token",
            repo_name="owner/repo",
            pr_number=99,
            head_sha="1234567890abcdef",
        )

        mock_client.merge_pull_request.assert_not_called()
        mock_client.enable_auto_merge.assert_not_called()
        # A hold still reports the gate check as successful, so any auto-merge queued by
        # an earlier pass must be cancelled or GitHub would merge a held PR.
        mock_client.disable_auto_merge.assert_called_once_with(repo="owner/repo", pr_number=99)
