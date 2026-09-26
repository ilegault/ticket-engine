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

# A passing ticket PR changes its tests; the gate holds one that doesn't (check 6).
_TEST_TOUCH_DIFF = (
    "diff --git a/tests/test_a.py b/tests/test_a.py\n"
    "--- a/tests/test_a.py\n"
    "+++ b/tests/test_a.py\n"
    "@@ -1 +1,2 @@\n"
    " def test_a(): assert True\n"
    "+# covers the ticket\n"
)


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
         patch("ticket_engine.integrity_runner.resolve_base_ref", side_effect=lambda _p, ref: ref), \
         patch("ticket_engine.integrity_runner.get_base_tree_from_git", return_value={"tests/test_a.py": "def test_a(): assert True\n"}), \
         patch("ticket_engine.integrity_runner.get_pr_diff_from_git", return_value=_TEST_TOUCH_DIFF), \
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
         patch("ticket_engine.integrity_runner.resolve_base_ref", side_effect=lambda _p, ref: ref), \
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


def test_run_integrity_gate_hold_returns_0_and_sets_a_green_status(tmp_path: pathlib.Path):
    # ADR 0005: a hold is green so the developer can merge it by hand; `pending` on a
    # required check left held PRs unmergeable. Only a pass enables auto-merge.
    mock_client = MagicMock()

    with patch("ticket_engine.integrity_runner.GitHubClient", return_value=mock_client), \
         patch("ticket_engine.integrity_runner.resolve_base_ref", side_effect=lambda _p, ref: ref), \
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
        assert status_call["state"] == "success"
        assert status_call["description"].startswith("HOLD, merge by hand: ")


def test_run_integrity_gate_pass_merges_pr(tmp_path: pathlib.Path):
    mock_client = MagicMock()

    with patch("ticket_engine.integrity_runner.GitHubClient", return_value=mock_client), \
         patch("ticket_engine.integrity_runner.resolve_base_ref", side_effect=lambda _p, ref: ref), \
         patch("ticket_engine.integrity_runner.get_base_tree_from_git", return_value={"tests/test_a.py": "def test_a(): assert True\n"}), \
         patch("ticket_engine.integrity_runner.get_pr_diff_from_git", return_value=_TEST_TOUCH_DIFF), \
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
         patch("ticket_engine.integrity_runner.resolve_base_ref", side_effect=lambda _p, ref: ref), \
         patch("ticket_engine.integrity_runner.get_base_tree_from_git", return_value={"tests/test_a.py": "def test_a(): assert True\n"}), \
         patch("ticket_engine.integrity_runner.get_pr_diff_from_git", return_value=_TEST_TOUCH_DIFF), \
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
         patch("ticket_engine.integrity_runner.resolve_base_ref", side_effect=lambda _p, ref: ref), \
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
         patch("ticket_engine.integrity_runner.resolve_base_ref", side_effect=lambda _p, ref: ref), \
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


def _git(repo: pathlib.Path, *args: str) -> None:
    import subprocess

    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _repo_with_done_ticket(tmp_path: pathlib.Path) -> pathlib.Path:
    """A base branch holding an old, already-done ticket, plus a PR branch."""
    repo = tmp_path / "repo"
    (repo / ".scratch/old-effort/issues").mkdir(parents=True)
    (repo / ".scratch/new-effort/issues").mkdir(parents=True)
    (repo / "src").mkdir()
    (repo / ".scratch/old-effort/issues/01-old.md").write_text(
        "# 01: Old\n**Status:** done\n## Acceptance criteria\n- [x] Done\n", encoding="utf-8"
    )
    (repo / ".scratch/new-effort/issues/35-new.md").write_text(
        "# 35: New\n**Status:** ready-for-agent\n## Acceptance criteria\n- [ ] Do it\n",
        encoding="utf-8",
    )
    (repo / "src/app.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    return repo


def test_find_ticket_content_ignores_tickets_the_pr_did_not_change(tmp_path: pathlib.Path):
    from ticket_engine.integrity_runner import find_ticket_content_from_git

    repo = _repo_with_done_ticket(tmp_path)
    (repo / "src/app.py").write_text("x = 2\n", encoding="utf-8")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "code only")

    # Must not fall back to the old done ticket and let the PR pass on its behalf.
    assert find_ticket_content_from_git(repo, "master") == ""


def test_find_ticket_content_returns_the_ticket_the_pr_changed(tmp_path: pathlib.Path):
    from ticket_engine.integrity_runner import find_ticket_content_from_git

    repo = _repo_with_done_ticket(tmp_path)
    done = "# 35: New\n**Status:** done\n## Acceptance criteria\n- [x] Do it\n"
    (repo / ".scratch/new-effort/issues/35-new.md").write_text(done, encoding="utf-8")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "ticket 35")

    assert find_ticket_content_from_git(repo, "master") == done


# --- Base ref as GitHub Actions presents it ---------------------------------
# On a pull_request run, actions/checkout leaves HEAD detached at the PR and has
# only remote-tracking branches: there is no local `master`, only `origin/master`.
# The workflow passes `github.base_ref`, which is the bare name `master`. Every git
# call against `master` then failed silently, so checks 1-4 and 7 compared the PR
# against nothing and check 6 never found the PR's ticket.


def _ci_shaped_clone(tmp_path: pathlib.Path, pr_ticket: str, pr_extra: dict | None = None) -> pathlib.Path:
    origin = tmp_path / "origin"
    (origin / ".scratch/e/issues").mkdir(parents=True)
    (origin / "tests").mkdir()
    (origin / "tests/test_a.py").write_text("def test_a():\n    assert 1 == 1\n", encoding="utf-8")
    (origin / ".scratch/e/issues/35-t.md").write_text(
        "# 35: T\n**Status:** ready-for-agent\n## Acceptance criteria\n- [ ] x\n", encoding="utf-8"
    )
    _git(origin, "init", "-q", "-b", "master")
    _git(origin, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    _git(origin, "checkout", "-q", "-b", "pr")
    (origin / ".scratch/e/issues/35-t.md").write_text(pr_ticket, encoding="utf-8")
    for rel, text in (pr_extra or {}).items():
        (origin / rel).write_text(text, encoding="utf-8")
    _git(origin, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-am", "pr")
    _git(origin, "checkout", "-q", "master")

    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "fetch", "-q", "origin", "pr")
    _git(clone, "checkout", "-q", "--detach", "FETCH_HEAD")
    _git(clone, "branch", "-q", "-D", "master")
    return clone


_DONE_TICKET = "# 35: T\n**Status:** done\n## Acceptance criteria\n- [x] x\n"


def test_resolve_base_ref_falls_back_to_the_remote_tracking_branch(tmp_path: pathlib.Path):
    from ticket_engine.integrity_runner import resolve_base_ref

    clone = _ci_shaped_clone(tmp_path, _DONE_TICKET)
    assert resolve_base_ref(clone, "master") == "origin/master"
    assert resolve_base_ref(clone, "origin/master") == "origin/master"


def test_resolve_base_ref_refuses_a_ref_that_does_not_exist(tmp_path: pathlib.Path):
    import pytest

    from ticket_engine.integrity_runner import resolve_base_ref

    clone = _ci_shaped_clone(tmp_path, _DONE_TICKET)
    with pytest.raises(ValueError, match="nope"):
        resolve_base_ref(clone, "nope")


def test_gate_finds_the_pr_ticket_when_given_a_bare_base_name(tmp_path: pathlib.Path):
    clone = _ci_shaped_clone(
        tmp_path,
        _DONE_TICKET,
        pr_extra={"tests/test_a.py": "def test_a():\n    assert 1 == 1\n    assert 2 == 2\n"},
    )
    summary = tmp_path / "summary.md"

    exit_code = run_integrity_gate(repo_path=clone, base_ref="master", step_summary_path=summary)

    text = summary.read_text(encoding="utf-8")
    assert "does not change any ticket file" not in text
    assert exit_code == 0
    assert "PASS" in text


def test_gate_sees_a_deleted_test_when_given_a_bare_base_name(tmp_path: pathlib.Path):
    # Check 2 must see the base's tests; before the fix the base tree was empty in CI.
    clone = _ci_shaped_clone(
        tmp_path, _DONE_TICKET, pr_extra={"tests/test_a.py": "# test removed\n"}
    )
    summary = tmp_path / "summary.md"

    exit_code = run_integrity_gate(repo_path=clone, base_ref="master", step_summary_path=summary)

    assert exit_code == 1
    assert "test_a" in summary.read_text(encoding="utf-8")


# --- Check 7 must not mistake "could not run" for "fails on base" -------------
# In CI the gate job once lacked the target repo's packages and its test env vars.
# Every new test crashed on import, the gate counted each crash as "failed on base",
# and check 7 passed in 0.08s having proved nothing. A new test only counts as
# failing on base if it first passes on the PR's own code in the same environment.

_TICKET_FOR_CHECK7 = "# 7: T\n**Status:** done\n## Acceptance criteria\n- [x] x\n"


def _check7_repo(tmp_path: pathlib.Path, new_test: str, extra: dict | None = None) -> pathlib.Path:
    """Base has src/lib.py with `old()`. The PR adds `new()`, the ticket and `new_test`."""
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / ".scratch/e/issues").mkdir(parents=True)
    (repo / "src/__init__.py").write_text("", encoding="utf-8")
    (repo / "src/lib.py").write_text("def old():\n    return 1\n", encoding="utf-8")
    (repo / "tests/test_old.py").write_text(
        "from src.lib import old\n\ndef test_old():\n    assert old() == 1\n", encoding="utf-8"
    )
    (repo / ".scratch/e/issues/07-t.md").write_text(
        "# 7: T\n**Status:** ready-for-agent\n## Acceptance criteria\n- [ ] x\n", encoding="utf-8"
    )
    _git(repo, "init", "-q", "-b", "master")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / "src/lib.py").write_text(
        "def old():\n    return 1\n\n\ndef new():\n    return 2\n", encoding="utf-8"
    )
    (repo / "tests/test_new.py").write_text(new_test, encoding="utf-8")
    (repo / ".scratch/e/issues/07-t.md").write_text(_TICKET_FOR_CHECK7, encoding="utf-8")
    for rel, text in (extra or {}).items():
        (repo / rel).write_text(text, encoding="utf-8")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", ".")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "pr")
    return repo


def _run_gate(repo: pathlib.Path, tmp_path: pathlib.Path) -> tuple[int, str]:
    summary = tmp_path / "summary.md"
    code = run_integrity_gate(repo_path=repo, base_ref="master", step_summary_path=summary)
    return code, summary.read_text(encoding="utf-8")


def test_check7_new_test_that_really_needs_the_feature_passes(tmp_path: pathlib.Path):
    repo = _check7_repo(
        tmp_path, "from src.lib import new\n\n\ndef test_new():\n    assert new() == 2\n"
    )
    code, text = _run_gate(repo, tmp_path)
    assert "Check 7 pass" in text, text
    assert code == 0


def test_check7_new_test_that_cannot_run_in_the_gate_env_holds(tmp_path: pathlib.Path):
    # Crashes on import on the PR's own code too, like a repo package missing in CI.
    repo = _check7_repo(
        tmp_path,
        "import package_the_gate_env_does_not_have  # noqa: F401\n\n\ndef test_new():\n    assert True\n",
    )
    code, text = _run_gate(repo, tmp_path)
    assert "Check 7 pass" not in text, text
    assert "did not pass on the PR's own code" in text
    assert "tests/test_new.py::test_new" in text
    assert "HOLD" in text
    assert code == 0


def test_check7_uses_the_repo_test_env_from_engine_config(tmp_path: pathlib.Path):
    # Slackbot's tests need dummy Slack tokens set before import; the repo declares
    # them once in its engine config and the gate sets them for check 7's runs.
    new_test = (
        "import os\n\nfrom src.lib import new\n\n\n"
        "def test_new():\n    assert os.environ['GATE_DEMO_TOKEN'] == 'not-a-secret'\n    assert new() == 2\n"
    )
    config = '[test_env]\nGATE_DEMO_TOKEN = "not-a-secret"\n'
    with_env = _check7_repo(tmp_path / "a", new_test, extra={".ticket-engine.toml": config})
    code, text = _run_gate(with_env, tmp_path / "a")
    assert "Check 7 pass" in text, text
    assert code == 0

    without_env = _check7_repo(tmp_path / "b", new_test)
    _, text = _run_gate(without_env, tmp_path / "b")
    assert "did not pass on the PR's own code" in text
