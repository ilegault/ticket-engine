"""Integrity gate runner adapter.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Adapters), Ticket 03 AC 5, and Ticket 05 AC 2
require running the pure integrity core in a git / GitHub Actions environment.
Check 7 (running new tests against the base branch's source code) executes in this adapter
layer to keep the core pure. This adapter queries git for base tree files, diffs, and
the PR's ticket file, swaps source paths to base_ref to run newly added tests with the repo's
configured test command, measures check runtime, invokes the pure IntegrityCore, formats the
verdict report, writes GitHub step summaries, and updates PR comments and commit statuses via
the GitHubClient. All I/O is kept in this adapter layer.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import pathlib
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
from collections.abc import Mapping, Sequence
from typing import Any

from ticket_engine.config import load_repo_config
from ticket_engine.github import INTEGRITY_COMMENT_MARKER, GitHubClient
from ticket_engine.integrity import (
    BaseTestResults,
    IntegrityConfig,
    IntegrityCore,
    IntegrityVerdict,
    find_new_test_functions,
)

logger = logging.getLogger(__name__)


def format_verdict_comment(verdict: IntegrityVerdict) -> str:
    """Format verdict and reasons into a markdown comment."""
    status_icon = "PASS" if verdict.is_pass() else ("FAIL" if verdict.is_fail() else "HOLD")
    lines = [
        INTEGRITY_COMMENT_MARKER,
        f"## Integrity Gate Verdict: {status_icon}",
        "",
        f"**Verdict:** `{verdict.verdict.value}`",
        "",
        "### Reasons:",
    ]
    for reason in verdict.reasons:
        lines.append(f"- {reason}")
    lines.append("")
    return "\n".join(lines)


def get_base_tree_from_git(
    repo_path: pathlib.Path,
    base_ref: str,
    test_dirs: tuple[str, ...] = ("tests",),
    ratchet_files: tuple[str, ...] = (),
) -> dict[str, str]:
    """Retrieve test files and ratchet files from git at base_ref."""
    base_tree: dict[str, str] = {}
    try:
        res = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", base_ref],
            cwd=repo_path,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if res.returncode != 0:
            return base_tree

        for line in res.stdout.splitlines():
            path_str = line.strip().replace("\\", "/")
            if path_str.endswith(".py") and any(
                path_str.startswith(td.rstrip("/") + "/") for td in test_dirs
            ):
                show_res = subprocess.run(
                    ["git", "show", f"{base_ref}:{path_str}"],
                    cwd=repo_path,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    text=True,
                    check=False,
                )
                if show_res.returncode == 0:
                    base_tree[path_str] = show_res.stdout

        for rf in ratchet_files:
            rf_norm = rf.strip().replace("\\", "/")
            show_res = subprocess.run(
                ["git", "show", f"{base_ref}:{rf_norm}"],
                cwd=repo_path,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            if show_res.returncode == 0:
                base_tree[rf_norm] = show_res.stdout
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Failed to query git base tree: %s", exc)

    return base_tree


def _ref_exists(repo_path: pathlib.Path, ref: str) -> bool:
    res = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"],
        cwd=repo_path,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        text=True,
        check=False,
    )
    return res.returncode == 0


def resolve_base_ref(repo_path: pathlib.Path, base_ref: str) -> str:
    """Return a base ref git can actually resolve, trying `origin/<ref>` second.

    WHY THIS EXISTS: on a pull_request run, actions/checkout leaves only
    remote-tracking branches (`origin/master`, no local `master`), while the
    workflow passes `github.base_ref`, the bare name `master`. Every git call
    against the bare name failed, and the helpers below treat a failed call as
    "nothing there", so checks 1-4 and 7 compared the PR against an empty base and
    check 6 never found the PR's ticket. A base that resolves to nothing is now an
    error, never an empty comparison.
    """
    ref = base_ref.strip()
    candidates = [ref]
    if not ref.startswith("origin/"):
        candidates.append(f"origin/{ref}")
    for candidate in candidates:
        if _ref_exists(repo_path, candidate):
            return candidate
    msg = f"Integrity gate: base ref {base_ref!r} not found (tried {', '.join(candidates)})"
    raise ValueError(msg)


def get_git_commit_messages(repo_path: pathlib.Path, base_ref: str) -> list[str]:
    """Retrieve commit messages between base_ref and HEAD."""
    try:
        res = subprocess.run(
            ["git", "log", "--pretty=%B", f"{base_ref}..HEAD"],
            cwd=repo_path,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            return [res.stdout]
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Failed to query git commit messages: %s", exc)
    return []



def get_pr_diff_from_git(repo_path: pathlib.Path, base_ref: str) -> str:
    """Retrieve unified diff between base_ref and HEAD."""
    try:
        res = subprocess.run(
            ["git", "diff", f"{base_ref}...HEAD"],
            cwd=repo_path,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Failed to query git diff: %s", exc)
    return ""


def find_ticket_content_from_git(repo_path: pathlib.Path, base_ref: str) -> str:
    """Find ticket markdown content modified by the PR, or "" if it changed none."""
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            cwd=repo_path,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                norm = line.strip().replace("\\", "/")
                if ".scratch/" in norm and "issues/" in norm and norm.endswith(".md"):
                    ticket_file = repo_path / norm
                    if ticket_file.is_file():
                        return ticket_file.read_text(encoding="utf-8")
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Failed to query git changed files for ticket: %s", exc)

    # No fallback to "any ticket file": that once picked an old, already-done ticket
    # and let a PR that never updated its own ticket pass check 6 and auto-merge.
    # An empty result makes check 6 fail with a reason the worker can act on.
    return ""


@contextlib.contextmanager
def temporary_base_source(
    repo_path: pathlib.Path,
    base_ref: str,
    source_paths: Sequence[str] = ("src",),
):
    """Context manager temporarily swapping source paths to base_ref."""
    with tempfile.TemporaryDirectory() as temp_backup_dir:
        backup_root = pathlib.Path(temp_backup_dir)
        backed_up: list[tuple[pathlib.Path, pathlib.Path, bool]] = []

        # 1. Back up current source paths and clear them
        for sp in source_paths:
            src_full = (repo_path / sp).resolve()
            if src_full.exists():
                dest = backup_root / sp
                dest.parent.mkdir(parents=True, exist_ok=True)
                if src_full.is_dir():
                    shutil.copytree(src_full, dest)
                    shutil.rmtree(src_full)
                    backed_up.append((src_full, dest, True))
                else:
                    shutil.copy2(src_full, dest)
                    src_full.unlink()
                    backed_up.append((src_full, dest, False))

        # 2. Checkout base_ref version of source paths from git
        for sp in source_paths:
            norm_sp = sp.replace("\\", "/").rstrip("/")
            ls_res = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", base_ref, "--", norm_sp],
                cwd=repo_path,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                check=False,
            )
            if ls_res.returncode == 0 and ls_res.stdout.strip():
                subprocess.run(
                    ["git", "checkout", base_ref, "--", norm_sp],
                    cwd=repo_path,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    check=False,
                )

        try:
            yield
        finally:
            # 3. Clean up any files checked out from base_ref
            for sp in source_paths:
                src_full = (repo_path / sp).resolve()
                if src_full.exists():
                    if src_full.is_dir():
                        shutil.rmtree(src_full)
                    else:
                        src_full.unlink()

            # 4. Restore backed up source paths
            for orig, dest, is_dir in backed_up:
                orig.parent.mkdir(parents=True, exist_ok=True)
                if is_dir:
                    shutil.copytree(dest, orig)
                else:
                    shutil.copy2(dest, orig)

            # 5. Clean git index if needed
            for sp in source_paths:
                subprocess.run(
                    ["git", "reset", "HEAD", "--", sp.replace("\\", "/")],
                    cwd=repo_path,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                    check=False,
                )


def execute_new_tests_on_base(
    repo_path: pathlib.Path,
    base_ref: str,
    new_tests: Sequence[str],
    test_command: str = "pytest",
    source_paths: Sequence[str] = ("src",),
    test_env: Mapping[str, str] | None = None,
) -> BaseTestResults:
    """Run new test functions on the PR's code, then on the base branch's source.

    WHY THIS EXISTS: check 7 needs each new test to fail on base. A test that crashes
    for an unrelated reason (a package or env var missing in the gate's environment)
    also "fails" on base, and the gate once passed check 7 in 0.08s with every new
    test crashing on import. So each new test must first pass on the PR's own code;
    one that doesn't is reported as unrunnable instead of as failing on base.
    """
    if not new_tests:
        return BaseTestResults(new_tests=[], passed_tests=[], failed_tests=[], runtime_seconds=0.0)

    base_cmd_parts = shlex.split(test_command)
    if not base_cmd_parts:
        base_cmd_parts = ["pytest"]

    cmd_name = base_cmd_parts[0]
    if cmd_name == "pytest":
        cmd_prefix = [sys.executable, "-m", "pytest"] + base_cmd_parts[1:]
    elif cmd_name == "python":
        cmd_prefix = [sys.executable] + base_cmd_parts[1:]
    else:
        cmd_prefix = list(base_cmd_parts)

    env = dict(os.environ)
    env.update(test_env or {})

    def _passes(test_node: str) -> bool:
        try:
            proc = subprocess.run(
                cmd_prefix + [test_node],
                cwd=repo_path,
                capture_output=True,
                stdin=subprocess.DEVNULL,
                text=True,
                check=False,
                env=env,
            )
        except (subprocess.SubprocessError, OSError) as exc:
            logger.warning("Error running test %s: %s", test_node, exc)
            return False
        return proc.returncode == 0

    t_start = time.perf_counter()

    runnable = [t for t in new_tests if _passes(t)]
    unrunnable = [t for t in new_tests if t not in runnable]
    if unrunnable:
        logger.warning("New tests that fail on the PR's own code in the gate env: %s", unrunnable)

    passed: list[str] = []
    failed: list[str] = []
    if runnable:
        with temporary_base_source(repo_path, base_ref, source_paths):
            for test_node in runnable:
                (passed if _passes(test_node) else failed).append(test_node)

    duration = max(0.0, time.perf_counter() - t_start)

    return BaseTestResults(
        new_tests=list(new_tests),
        passed_tests=passed,
        failed_tests=failed,
        runtime_seconds=duration,
        unrunnable_tests=unrunnable,
    )


def run_integrity_gate(
    repo_path: pathlib.Path,
    base_ref: str = "origin/master",
    ticket_path: pathlib.Path | None = None,
    token: str | None = None,
    repo_name: str | None = None,
    pr_number: int | None = None,
    head_sha: str | None = None,
    step_summary_path: pathlib.Path | None = None,
    test_results: Any = None,
    labels: list[str] | None = None,
    commit_messages: list[str] | None = None,
    pr_text: str | None = None,
    config: IntegrityConfig | None = None,
    base_test_results: BaseTestResults | None = None,
) -> int:
    """Execute integrity gate checks and report verdict."""
    base_ref = resolve_base_ref(repo_path, base_ref)
    repo_cfg = load_repo_config(repo_path)
    cfg = config or IntegrityConfig(
        test_paths=repo_cfg.test_paths,
        src_paths=repo_cfg.source_paths,
        ratchet_files=repo_cfg.ratchet_paths,
    )
    base_tree = get_base_tree_from_git(
        repo_path, base_ref, tuple(cfg.test_paths), tuple(cfg.ratchet_files)
    )
    pr_diff = get_pr_diff_from_git(repo_path, base_ref)

    if ticket_path and ticket_path.is_file():
        ticket_content = ticket_path.read_text(encoding="utf-8")
    else:
        ticket_content = find_ticket_content_from_git(repo_path, base_ref)

    actual_commit_messages = (
        commit_messages
        if commit_messages is not None
        else get_git_commit_messages(repo_path, base_ref)
    )

    # Check 7: Run new tests against base source
    if base_test_results is None:
        new_tests = find_new_test_functions(base_tree, pr_diff, cfg.test_paths)
        if new_tests:
            actual_base_results = execute_new_tests_on_base(
                repo_path=repo_path,
                base_ref=base_ref,
                new_tests=new_tests,
                test_command=repo_cfg.test_command,
                source_paths=cfg.src_paths,
                test_env=repo_cfg.test_env,
            )
        else:
            actual_base_results = None
    else:
        actual_base_results = base_test_results

    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
        config=cfg,
        test_results=test_results,
        labels=labels,
        commit_messages=actual_commit_messages,
        pr_text=pr_text,
        base_test_results=actual_base_results,
    )

    comment_body = format_verdict_comment(verdict)
    print(comment_body)

    if step_summary_path:
        try:
            with open(step_summary_path, "a", encoding="utf-8") as f:
                f.write(comment_body + "\n")
        except OSError as exc:
            logger.warning("Failed to write to step summary: %s", exc)

    # Post or update PR comment and commit status if token and repo info provided
    if token and repo_name:
        client = GitHubClient(token=token)
        if pr_number:
            try:
                client.post_or_update_pr_comment(
                    repo=repo_name,
                    pr_number=pr_number,
                    body=comment_body,
                )
            except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, RuntimeError) as exc:
                logger.error("Failed to post or update PR comment: %s", exc)

        if head_sha:
            status_state = (
                "success"
                if verdict.is_pass()
                else ("failure" if verdict.is_fail() else "pending")
            )
            desc = (
                verdict.reasons[0]
                if verdict.reasons
                else f"Verdict: {verdict.verdict.value}"
            )
            try:
                client.set_commit_status(
                    repo=repo_name,
                    sha=head_sha,
                    state=status_state,
                    description=desc,
                )
            except (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, RuntimeError) as exc:
                logger.error("Failed to set commit status: %s", exc)

        # ADR 0001 §1: a PR whose CI is green and whose integrity verdict is
        # `pass` merges automatically. This job is itself a required check and is
        # still running right now, so an immediate merge is always refused. Hand
        # the merge to GitHub's native auto-merge instead: it merges once every
        # required check (the gate and CI) is green, and that merge's push wakes
        # the dispatcher for the next ticket. If GitHub won't queue it (e.g. the
        # PR is already mergeable), fall back to merging directly.
        net_errors = (OSError, urllib.error.URLError, json.JSONDecodeError, KeyError, ValueError, RuntimeError)
        if pr_number and verdict.is_pass():
            try:
                queued = client.enable_auto_merge(
                    repo=repo_name,
                    pr_number=pr_number,
                    merge_method=repo_cfg.merge_method,
                )
                if not queued:
                    client.merge_pull_request(
                        repo=repo_name,
                        pr_number=pr_number,
                        merge_method=repo_cfg.merge_method,
                    )
            except net_errors as exc:
                logger.error("Failed to auto-merge PR #%s: %s", pr_number, exc)
        elif pr_number:
            # A hold still reports this check as successful, and a failing new push
            # may follow an earlier pass: cancel any auto-merge an earlier run queued.
            try:
                client.disable_auto_merge(repo=repo_name, pr_number=pr_number)
            except net_errors as exc:
                logger.error("Failed to cancel auto-merge on PR #%s: %s", pr_number, exc)

    return 1 if verdict.is_fail() else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ticket-engine integrity gate")
    parser.add_argument(
        "--repo-path",
        type=pathlib.Path,
        default=pathlib.Path("."),
        help="Path to repository root",
    )
    parser.add_argument(
        "--base-ref",
        default=os.environ.get("GITHUB_BASE_REF") or "origin/master",
        help="Base git reference (e.g. origin/master)",
    )
    parser.add_argument(
        "--ticket",
        type=pathlib.Path,
        default=None,
        help="Explicit path to ticket markdown file",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("PIPELINE_TOKEN") or os.environ.get("GITHUB_TOKEN"),
        help="GitHub token for PR comment and status updates",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="Target repo in owner/repo format",
    )
    parser.add_argument(
        "--pr-number",
        type=int,
        default=None,
        help="Pull request number",
    )
    parser.add_argument(
        "--sha",
        default=os.environ.get("GITHUB_SHA"),
        help="Commit SHA for check status",
    )
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        help="PR label to evaluate for test exemptions",
    )
    parser.add_argument(
        "--commit-message",
        action="append",
        default=[],
        help="Commit message to evaluate for escape hatch tags",
    )
    args = parser.parse_args(argv)

    event_path = os.environ.get("GITHUB_EVENT_PATH")
    pr_num = args.pr_number
    head_sha = args.sha
    event_labels: list[str] = []
    event_texts: list[str] = []

    if event_path and pathlib.Path(event_path).is_file():
        try:
            event_data = json.loads(pathlib.Path(event_path).read_text(encoding="utf-8"))
            pr = event_data.get("pull_request")
            if pr:
                if not pr_num and "number" in pr:
                    pr_num = int(pr["number"])
                if not head_sha and "head" in pr and "sha" in pr["head"]:
                    head_sha = pr["head"]["sha"]
                for lbl in pr.get("labels", []):
                    if isinstance(lbl, dict) and "name" in lbl:
                        event_labels.append(lbl["name"])
                    elif isinstance(lbl, str):
                        event_labels.append(lbl)
                if pr.get("title"):
                    event_texts.append(str(pr["title"]))
                if pr.get("body"):
                    event_texts.append(str(pr["body"]))
        except (OSError, json.JSONDecodeError, KeyError, ValueError) as exc:
            logger.warning("Could not read event payload: %s", exc)

    all_labels = list(args.label) + event_labels
    all_texts = list(args.commit_message) + event_texts
    pr_text = "\n".join(all_texts) if all_texts else None

    step_summary_path = (
        pathlib.Path(os.environ["GITHUB_STEP_SUMMARY"])
        if "GITHUB_STEP_SUMMARY" in os.environ
        else None
    )

    return run_integrity_gate(
        repo_path=args.repo_path,
        base_ref=args.base_ref,
        ticket_path=args.ticket,
        token=args.token,
        repo_name=args.repo,
        pr_number=pr_num,
        head_sha=head_sha,
        step_summary_path=step_summary_path,
        labels=all_labels if all_labels else None,
        commit_messages=args.commit_message if args.commit_message else None,
        pr_text=pr_text,
    )



if __name__ == "__main__":
    sys.exit(main())
