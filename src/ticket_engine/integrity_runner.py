"""Integrity gate runner adapter.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Adapters) and Ticket 03 Acceptance Criterion 5
require running the pure integrity core in a git / GitHub Actions environment.
This adapter queries git for base tree files, diffs, and the PR's ticket file,
invokes the pure IntegrityCore, formats the verdict report, writes GitHub step
summaries, and updates PR comments and commit statuses via the GitHubClient.
All I/O is kept in this adapter layer.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pathlib
import subprocess
import sys
import urllib.error
from typing import Any

from ticket_engine.github import INTEGRITY_COMMENT_MARKER, GitHubClient
from ticket_engine.integrity import (
    IntegrityConfig,
    IntegrityCore,
    IntegrityVerdict,
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
                text=True,
                check=False,
            )
            if show_res.returncode == 0:
                base_tree[rf_norm] = show_res.stdout
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Failed to query git base tree: %s", exc)

    return base_tree


def get_git_commit_messages(repo_path: pathlib.Path, base_ref: str) -> list[str]:
    """Retrieve commit messages between base_ref and HEAD."""
    try:
        res = subprocess.run(
            ["git", "log", "--pretty=%B", f"{base_ref}..HEAD"],
            cwd=repo_path,
            capture_output=True,
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
            text=True,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout
    except (subprocess.SubprocessError, OSError) as exc:
        logger.warning("Failed to query git diff: %s", exc)
    return ""


def find_ticket_content_from_git(repo_path: pathlib.Path, base_ref: str) -> str:
    """Find ticket markdown content modified by the PR."""
    try:
        res = subprocess.run(
            ["git", "diff", "--name-only", f"{base_ref}...HEAD"],
            cwd=repo_path,
            capture_output=True,
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

    # Fallback to local files under .scratch
    for p in repo_path.glob(".scratch/*/issues/*.md"):
        if p.is_file():
            return p.read_text(encoding="utf-8")

    return ""


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
    denylist: list[str] | str | None = None,
    config: IntegrityConfig | None = None,
) -> int:
    """Execute integrity gate checks and report verdict."""
    cfg = config or IntegrityConfig()
    actual_denylist = denylist if denylist is not None else os.environ.get("PEOPLE_DENYLIST")

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
        denylist=actual_denylist,
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
    parser.add_argument(
        "--denylist",
        default=None,
        help="Explicit denylist string or entries",
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
        denylist=args.denylist,
    )



if __name__ == "__main__":
    sys.exit(main())
