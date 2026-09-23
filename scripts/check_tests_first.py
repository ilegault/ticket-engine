"""Enforce the tests-first rule in CI.

Any change to application source (src/) must be accompanied by changes in tests (tests/),
unless explicitly exempted.

WHY THIS EXISTS
---------------
ADR 0001 establishes that tests are written before code. In unattended overnight runs,
an implementing agent is rewarded for a green build, and the easiest way to turn a red
build green is to stop tests from reporting problems or skip writing them.
This check runs in CI (where it binds every author and PR) to ensure that PRs touching
application code under `src/` also touch tests under `tests/`.

NON-APPLICATION CHANGES PASS AUTOMATICALLY
------------------------------------------
Changes touching only documentation (docs/), CI workflows (.github/), build scripts
(scripts/), or test files (tests/) pass without requiring test modifications.

EXPLICIT ESCAPE MECHANISMS
--------------------------
For legitimate changes that do not require test modifications:
1. PR Label: Add the `tests-exempt` or `skip-test-gate` label.
2. Commit Message or PR Description: Include an explicit annotation tag:
   `[no-test-needed: <reason>]`, `[tests-exempt: <reason>]`, or `[skip-test-gate]`.
3. CLI / Script argument: `--exempt-reason "<reason>"`.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

_ESCAPE_TAG_RE = re.compile(
    r"\[(?:no-test-needed|tests-exempt|skip-test-gate)(?::\s*([^\]]+))?\]",
    re.IGNORECASE,
)
_EXEMPT_LABELS = {"tests-exempt", "skip-test-gate", "no-test-needed"}


def normalize_path(p: str | pathlib.Path) -> str:
    """Normalize a path to use forward slashes and strip leading ./"""
    s = str(p).replace("\\", "/").strip()
    s = s.removeprefix("./")
    return s


def categorize_files(
    files: Iterable[str | pathlib.Path],
) -> tuple[list[str], list[str], list[str]]:
    """Split a list of file paths into (src_files, test_files, other_files)."""
    src_files: list[str] = []
    test_files: list[str] = []
    other_files: list[str] = []

    for f in files:
        norm = normalize_path(f)
        if not norm:
            continue
        if norm.startswith("src/"):
            src_files.append(norm)
        elif norm.startswith("tests/"):
            test_files.append(norm)
        else:
            other_files.append(norm)

    return src_files, test_files, other_files


def find_escape_reasons(
    texts: Iterable[str] | None = None,
    labels: Iterable[str] | None = None,
    explicit_reason: str | None = None,
) -> list[str]:
    """Extract all valid escape reasons from texts, PR labels, or explicit reason."""
    reasons: list[str] = []

    if explicit_reason:
        reasons.append(f"Explicit exemption: {explicit_reason.strip()}")

    if labels:
        for lbl in labels:
            if lbl.strip().lower() in _EXEMPT_LABELS:
                reasons.append(f"PR label '{lbl.strip()}'")

    if texts:
        for text in texts:
            if not text:
                continue
            for match in _ESCAPE_TAG_RE.finditer(text):
                tag_reason = match.group(1)
                full_tag = match.group(0)
                if tag_reason and tag_reason.strip():
                    reasons.append(f"Annotation {full_tag} with reason: '{tag_reason.strip()}'")
                else:
                    reasons.append(f"Annotation {full_tag}")

    return reasons


def evaluate_tests_first(
    changed_files: Iterable[str | pathlib.Path],
    escape_reasons: Sequence[str] | None = None,
) -> tuple[bool, str]:
    """Evaluate whether changed files comply with the tests-first rule.

    Returns:
        (passed: bool, message: str)
    """
    src_files, test_files, other_files = categorize_files(changed_files)

    if not src_files:
        msg = (
            "OK: No application source files ('src/') modified. "
            f"({len(test_files)} test file(s), {len(other_files)} other file(s) changed)"
        )
        return True, msg

    if src_files and test_files:
        msg = (
            f"OK: Application source changes ({len(src_files)} file(s)) accompanied by "
            f"test changes ({len(test_files)} file(s))."
        )
        return True, msg

    if escape_reasons:
        reasons_str = "; ".join(escape_reasons)
        msg = (
            f"OK (EXEMPT): Application source changes ({len(src_files)} file(s)) have no "
            f"test changes, but valid exemption was found:\n  - {reasons_str}"
        )
        return True, msg

    file_list = "\n  - ".join(src_files)
    msg = (
        "FAIL: Application source files modified under 'src/' without corresponding "
        "test changes under 'tests/':\n"
        f"  - {file_list}\n\n"
        "Tests-first rule violation (see docs/adr/0001-auto-merge-on-green-behind-an-integrity-gate.md):\n"
        "1. Write tests under 'tests/' covering the application changes.\n"
        "2. If this change is genuinely exempt from test updates, provide an explicit "
        "escape reason:\n"
        "   - PR Label: 'tests-exempt' or 'skip-test-gate'\n"
        "   - Commit message or PR body: [no-test-needed: <reason>], [tests-exempt: <reason>], "
        "or [skip-test-gate]\n"
    )
    return False, msg


def extract_event_info(
    event_path: pathlib.Path | str | None,
) -> tuple[list[str], list[str], str | None]:
    """Extract labels, texts (title/body), and base ref/sha from GitHub event JSON."""
    labels: list[str] = []
    texts: list[str] = []
    base_ref: str | None = None

    if not event_path:
        return labels, texts, base_ref

    p = pathlib.Path(event_path)
    if not p.is_file():
        return labels, texts, base_ref

    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return labels, texts, base_ref

    pr = data.get("pull_request")
    if pr and isinstance(pr, dict):
        if pr.get("title"):
            texts.append(str(pr["title"]))
        if pr.get("body"):
            texts.append(str(pr["body"]))
        for lbl in pr.get("labels", []):
            if isinstance(lbl, dict) and "name" in lbl:
                labels.append(lbl["name"])
            elif isinstance(lbl, str):
                labels.append(lbl)
        base = pr.get("base")
        if isinstance(base, dict):
            base_ref = base.get("sha") or base.get("ref")

    return labels, texts, base_ref


def get_git_changed_files(
    base: str | None = None,
    head: str | None = None,
    cwd: pathlib.Path = _REPO_ROOT,
) -> list[str]:
    """Query git for list of changed files."""
    diff_target = f"{base}...{head or 'HEAD'}" if base else None

    cmd_candidates = []
    if diff_target:
        cmd_candidates.append(["git", "diff", "--name-only", diff_target])
    else:
        base_ref = os.environ.get("GITHUB_BASE_REF")
        if base_ref:
            cmd_candidates.append(["git", "diff", "--name-only", f"origin/{base_ref}...HEAD"])
            cmd_candidates.append(["git", "diff", "--name-only", f"{base_ref}...HEAD"])

        cmd_candidates.append(["git", "diff", "--name-only", "origin/master...HEAD"])
        cmd_candidates.append(["git", "diff", "--name-only", "origin/main...HEAD"])
        cmd_candidates.append(["git", "diff", "--name-only", "HEAD~1...HEAD"])
        cmd_candidates.append(["git", "diff", "--name-only", "HEAD"])
        cmd_candidates.append(["git", "status", "--porcelain"])

    for cmd in cmd_candidates:
        try:
            res = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                if cmd[1] == "status":
                    files = []
                    for line in lines:
                        parts = line.split(maxsplit=1)
                        if len(parts) == 2:
                            files.append(parts[1])
                    return files
                if lines or diff_target:
                    return lines
        except (subprocess.SubprocessError, OSError):
            continue

    return []


def get_git_commit_messages(
    base: str | None = None,
    head: str | None = None,
    cwd: pathlib.Path = _REPO_ROOT,
) -> list[str]:
    """Query git for commit messages between base and head."""
    range_str = f"{base}..{head or 'HEAD'}" if base else None
    cmd_candidates = []
    if range_str:
        cmd_candidates.append(["git", "log", "--pretty=%B", range_str])
    else:
        base_ref = os.environ.get("GITHUB_BASE_REF")
        if base_ref:
            cmd_candidates.append(["git", "log", "--pretty=%B", f"origin/{base_ref}..HEAD"])
        cmd_candidates.append(["git", "log", "--pretty=%B", "origin/master..HEAD"])
        cmd_candidates.append(["git", "log", "-1", "--pretty=%B"])

    for cmd in cmd_candidates:
        try:
            res = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                return [res.stdout]
        except (subprocess.SubprocessError, OSError):
            continue

    return []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check tests-first compliance for pull requests and commits.",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        help="Explicit list of changed files to check.",
    )
    parser.add_argument(
        "--message",
        action="append",
        default=[],
        help="Commit message or PR text to scan for escape annotations.",
    )
    parser.add_argument(
        "--label",
        action="append",
        default=[],
        help="PR label to check for test exemptions.",
    )
    parser.add_argument(
        "--exempt-reason",
        help="Explicit exemption reason string.",
    )
    parser.add_argument(
        "--base",
        help="Git base reference for diff comparison (e.g. origin/master).",
    )
    parser.add_argument(
        "--head",
        help="Git head reference for diff comparison (e.g. HEAD).",
    )
    parser.add_argument(
        "--event-path",
        default=os.environ.get("GITHUB_EVENT_PATH"),
        help="Path to GitHub Actions event payload JSON file.",
    )
    parser.add_argument(
        "--repo-root",
        type=pathlib.Path,
        default=_REPO_ROOT,
        help="Path to repository root.",
    )

    args = parser.parse_args(argv)

    event_labels, event_texts, event_base = extract_event_info(args.event_path)

    all_labels = list(args.label) + event_labels
    all_texts = list(args.message) + event_texts

    base_ref = args.base or event_base

    if args.files is not None:
        changed_files = args.files
    else:
        changed_files = get_git_changed_files(base=base_ref, head=args.head, cwd=args.repo_root)
        if not all_texts:
            all_texts.extend(
                get_git_commit_messages(base=base_ref, head=args.head, cwd=args.repo_root)
            )

    escape_reasons = find_escape_reasons(
        texts=all_texts,
        labels=all_labels,
        explicit_reason=args.exempt_reason,
    )

    passed, message = evaluate_tests_first(changed_files, escape_reasons=escape_reasons)
    print(message)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
