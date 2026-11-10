"""Enforce the tests-first rule in CI.

A change to application source (the folders in `source_paths`, default `src/`) must
be accompanied by changes under `tests/`, unless it is explicitly exempted.

WHY THIS EXISTS
---------------
ADR 0001 establishes that tests are written before code. In unattended overnight
runs, an implementing agent is rewarded for a green build, and the easiest way to
turn a red build green is to stop tests from reporting problems or skip writing
them. This check runs in CI, where it binds every author and PR.

This file is copied unedited into every target repo (bootstrap writes it byte for
byte), so it is stdlib-only and keeps every line at 88 characters or fewer, the
default line length of the strictest target repo's ruff.

The check was found to have holes (.scratch/tests-first-gate-holes/spec.md):

- It failed open. When git could not work out what changed (shallow checkout, no
  origin ref, not a repo), it returned an empty list and an empty list passed. Now
  it FAILs: "no changes found" and "could not look" are different answers.
- A commit-message tag was ignored on PR runs, and one tag exempted every file,
  including code added later by untagged commits. Now a commit tag covers only the
  files that commit changed, and a file is exempt only when EVERY commit that
  changed it is tagged.
- `src/` was hard-coded. Now the folders come from `.ticket-engine.toml`.
- A docstring or comment edit counted as code. Now a `.py` file whose syntax tree
  is unchanged once docstrings are removed is not a code change.

SHAPE
-----
`decide_tests_first` and `code_fingerprint` are pure: no git, no clock. The git
calls live in `_git` and the functions around it, which only fetch facts and hand
them to the pure part.

NON-APPLICATION CHANGES PASS AUTOMATICALLY
------------------------------------------
Changes touching only documentation (docs/), CI workflows (.github/), build
scripts (scripts/), or test files (tests/) pass without requiring test changes.

EXPLICIT ESCAPE MECHANISMS
--------------------------
Two reaches. These cover the whole run:
1. PR label: `tests-exempt` or `skip-test-gate`.
2. A tag in the PR title or body: `[no-test-needed: <reason>]`,
   `[tests-exempt: <reason>]`, or `[skip-test-gate]`.
3. CLI arguments: `--exempt-reason`, `--message`, `--label`.
This covers only the files a commit changed (every commit that changed the file
must carry it):
4. The same tag in a commit message.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import pathlib
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent

_ESCAPE_TAG_RE = re.compile(
    r"\[(?:no-test-needed|tests-exempt|skip-test-gate)(?::\s*([^\]]+))?\]",
    re.IGNORECASE,
)
_EXEMPT_LABELS = {"tests-exempt", "skip-test-gate", "no-test-needed"}
_DEFAULT_SOURCE_PATHS = ("src/",)
_FULL_HISTORY_HINT = (
    "CI must check out full history (actions/checkout with fetch-depth: 0)."
)


@dataclass(frozen=True)
class RepoSettings:
    """The slice of `.ticket-engine.toml` this script needs."""

    source_paths: tuple[str, ...]
    default_branch: str | None


@dataclass(frozen=True)
class CommitInfo:
    sha: str
    subject: str
    escape_reasons: tuple[str, ...]


@dataclass(frozen=True)
class SourceChange:
    path: str
    is_code: bool
    commits: tuple[CommitInfo, ...]


class GitError(Exception):
    """A git command failed; the message names the command and its first error."""


def normalize_path(p: str | pathlib.Path) -> str:
    """Normalize a path to use forward slashes and strip leading ./"""
    s = str(p).replace("\\", "/").strip()
    s = s.removeprefix("./")
    return s


def _source_prefix(raw: object) -> str:
    if not isinstance(raw, str):
        raise TypeError("source_paths must be a list of folder names")
    folder = normalize_path(raw).rstrip("/")
    if folder in ("", "."):
        raise ValueError("source_paths entries must name a folder")
    return folder + "/"


def load_repo_settings(repo_root: pathlib.Path) -> RepoSettings:
    """Read `.ticket-engine.toml`. Missing file: defaults. Bad file: raises."""
    path = pathlib.Path(repo_root) / ".ticket-engine.toml"
    if not path.is_file():
        return RepoSettings(_DEFAULT_SOURCE_PATHS, None)
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    raw_paths = data.get("source_paths", ["src"])
    if not isinstance(raw_paths, list):
        raise TypeError("source_paths must be a list of folder names")
    paths = tuple(_source_prefix(p) for p in raw_paths) or _DEFAULT_SOURCE_PATHS
    branch = data.get("default_branch")
    return RepoSettings(paths, str(branch) if branch else None)


def _quoted(source_paths: Iterable[str]) -> str:
    return ", ".join(f"'{p}'" for p in source_paths)


def categorize_files(
    files: Iterable[str | pathlib.Path],
    source_paths: Sequence[str] = _DEFAULT_SOURCE_PATHS,
) -> tuple[list[str], list[str], list[str]]:
    """Split a list of file paths into (src_files, test_files, other_files)."""
    prefixes = tuple(source_paths)
    src_files: list[str] = []
    test_files: list[str] = []
    other_files: list[str] = []

    for f in files:
        norm = normalize_path(f)
        if not norm:
            continue
        if norm.startswith(prefixes):
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
                    reasons.append(
                        f"Annotation {full_tag} with reason: '{tag_reason.strip()}'"
                    )
                else:
                    reasons.append(f"Annotation {full_tag}")

    return reasons


def _fail_message(file_list: str, source_paths: Sequence[str]) -> str:
    return (
        f"FAIL: Application source files modified under {_quoted(source_paths)} "
        "without corresponding test changes under 'tests/':\n"
        f"  - {file_list}\n\n"
        "Tests-first rule violation (see ADR 0001 under docs/adr/):\n"
        "1. Write tests under 'tests/' covering the application changes.\n"
        "2. If this change is genuinely exempt from test updates, provide an "
        "explicit escape reason:\n"
        "   - PR Label: 'tests-exempt' or 'skip-test-gate'\n"
        "   - Commit message or PR body: [no-test-needed: <reason>], "
        "[tests-exempt: <reason>], or [skip-test-gate]\n"
        "     (a commit tag covers only the files that commit changed)\n"
    )


def evaluate_tests_first(
    changed_files: Iterable[str | pathlib.Path],
    escape_reasons: Sequence[str] | None = None,
    source_paths: Sequence[str] = _DEFAULT_SOURCE_PATHS,
) -> tuple[bool, str]:
    """Evaluate whether changed files comply with the tests-first rule.

    Every listed source file counts as code (no git, no docstring check).

    Returns:
        (passed: bool, message: str)
    """
    src_files, test_files, other_files = categorize_files(
        changed_files, source_paths
    )

    if not src_files:
        msg = (
            f"OK: No application source files ({_quoted(source_paths)}) modified. "
            f"({len(test_files)} test file(s), {len(other_files)} other file(s) "
            "changed)"
        )
        return True, msg

    if src_files and test_files:
        msg = (
            f"OK: Application source changes ({len(src_files)} file(s)) "
            f"accompanied by test changes ({len(test_files)} file(s))."
        )
        return True, msg

    if escape_reasons:
        reasons_str = "; ".join(escape_reasons)
        msg = (
            f"OK (EXEMPT): Application source changes ({len(src_files)} file(s)) "
            "have no test changes, but valid exemption was found:\n"
            f"  - {reasons_str}"
        )
        return True, msg

    return False, _fail_message("\n  - ".join(src_files), source_paths)


def code_fingerprint(source: str) -> str | None:
    """A string equal for two sources that differ only in docstrings and comments.

    Comments never reach the syntax tree. Docstrings do, so the first statement
    of the module and of every class and function is dropped when it is a string
    constant. Returns None when the source does not parse.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
    for node in ast.walk(tree):
        if not isinstance(node, holders) or not node.body:
            continue
        first = node.body[0]
        is_doc = (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        )
        if is_doc:
            node.body = node.body[1:]
    return ast.dump(tree)


def decide_tests_first(
    changes: Sequence[SourceChange],
    tests_changed: bool,
    run_escapes: Sequence[str],
    source_paths: Sequence[str],
) -> tuple[bool, str]:
    """The git-mode verdict. Pure: every rule is testable without git."""
    code = [c for c in changes if c.is_code]
    non_code = [c.path for c in changes if not c.is_code]

    if not code:
        msg = f"OK: No code changes under {_quoted(source_paths)}."
        if non_code:
            msg += f" Docstring/comment-only: {', '.join(non_code)}."
        return True, msg

    if tests_changed:
        return True, (
            f"OK: Application source changes ({len(code)} file(s)) "
            "accompanied by test changes."
        )

    if run_escapes:
        return True, (
            f"OK (EXEMPT): Application source changes ({len(code)} file(s)) "
            "have no test changes, but valid exemption was found:\n"
            f"  - {'; '.join(run_escapes)}"
        )

    def covered(change: SourceChange) -> bool:
        return bool(change.commits) and all(
            c.escape_reasons for c in change.commits
        )

    uncovered = [c for c in code if not covered(c)]
    if not uncovered:
        lines = []
        for change in code:
            seen: list[str] = []
            for commit in change.commits:
                for reason in commit.escape_reasons:
                    if reason not in seen:
                        seen.append(reason)
            lines.append(f"  - {change.path}: {'; '.join(seen)}")
        return True, (
            "OK (EXEMPT): every commit that changed these source files "
            "carries an exemption tag:\n" + "\n".join(lines)
        )

    entries = []
    for change in uncovered:
        entry = change.path
        for commit in change.commits:
            if not commit.escape_reasons:
                entry += (
                    f"\n      untagged commit {commit.sha[:7]} {commit.subject}"
                )
        entries.append(entry)
    return False, _fail_message("\n  - ".join(entries), source_paths)


def _load_event(event_path: pathlib.Path | str | None) -> dict | None:
    if not event_path:
        return None
    p = pathlib.Path(event_path)
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def extract_event_info(
    event_path: pathlib.Path | str | None,
) -> tuple[list[str], list[str], str | None]:
    """Extract labels, texts (title/body), and base ref/sha from GitHub event JSON."""
    labels: list[str] = []
    texts: list[str] = []
    base_ref: str | None = None

    data = _load_event(event_path)
    if data is None:
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


def extract_event_head(event_path: pathlib.Path | str | None) -> str | None:
    """The PR head sha from a GitHub event payload, if there is one."""
    data = _load_event(event_path)
    pr = data.get("pull_request") if data else None
    head = pr.get("head") if isinstance(pr, dict) else None
    if isinstance(head, dict) and head.get("sha"):
        return str(head["sha"])
    return None


def _git(args: Sequence[str], cwd: pathlib.Path) -> str:
    cmd = " ".join(["git", *args])
    try:
        res = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise GitError(f"{cmd} could not run: {exc}") from exc
    if res.returncode != 0:
        lines = res.stderr.strip().splitlines()
        first = lines[0] if lines else "(no error output)"
        raise GitError(f"{cmd} exited {res.returncode}: {first}")
    return res.stdout


def _resolve_base(
    explicit: str | None, settings: RepoSettings, cwd: pathlib.Path
) -> str:
    if explicit:
        return explicit
    if settings.default_branch:
        candidates = [f"origin/{settings.default_branch}"]
    else:
        candidates = ["origin/master", "origin/main"]
    last_error: GitError | None = None
    for ref in candidates:
        try:
            _git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"], cwd)
        except GitError as exc:
            last_error = exc
            continue
        return ref
    assert last_error is not None
    raise last_error


def _range_base(base: str, head: str, cwd: pathlib.Path) -> str | None:
    """The commit the changes are measured from; None for a root commit."""
    merge_base = _git(["merge-base", base, head], cwd).strip()
    head_sha = _git(["rev-parse", head], cwd).strip()
    if merge_base != head_sha:
        return merge_base
    # A push to the base branch itself: check the last commit.
    parent = f"{head}~1"
    try:
        _git(["rev-parse", "--verify", "--quiet", f"{parent}^{{commit}}"], cwd)
    except GitError:
        if _git(["rev-parse", "--is-shallow-repository"], cwd).strip() == "true":
            raise
        return None
    return parent


def _commits_for(
    path: str, range_base: str, head: str, cwd: pathlib.Path
) -> tuple[CommitInfo, ...]:
    out = _git(
        [
            "log",
            "--format=%H%x00%s%x00%B%x1e",
            f"{range_base}..{head}",
            "--",
            path,
        ],
        cwd,
    )
    commits: list[CommitInfo] = []
    for record in out.split("\x1e"):
        record = record.lstrip("\n")
        if not record.strip():
            continue
        sha, _, rest = record.partition("\x00")
        subject, _, body = rest.partition("\x00")
        reasons = find_escape_reasons(texts=[body])
        commits.append(CommitInfo(sha, subject, tuple(reasons)))
    return tuple(commits)


def _is_code_change(
    path: str, range_base: str, head: str, cwd: pathlib.Path
) -> bool:
    """False only for a `.py` file whose code is unchanged but for docstrings."""
    if not path.endswith(".py"):
        return True
    try:
        before = _git(["show", f"{range_base}:{path}"], cwd)
        after = _git(["show", f"{head}:{path}"], cwd)
    except GitError:
        return True
    fp_before = code_fingerprint(before)
    fp_after = code_fingerprint(after)
    return fp_before is None or fp_before != fp_after


def _git_mode(
    args: argparse.Namespace,
    settings: RepoSettings,
    event_base: str | None,
    run_escapes: Sequence[str],
) -> tuple[bool, str]:
    cwd = args.repo_root
    head = args.head or extract_event_head(args.event_path) or "HEAD"
    base = _resolve_base(args.base or event_base, settings, cwd)
    range_base = _range_base(base, head, cwd)
    if range_base is None:
        return True, "OK: nothing to compare (root commit)."

    raw = _git(["diff", "--name-only", "-z", range_base, head], cwd)
    changed = [p for p in raw.split("\0") if p]
    src_files, test_files, _ = categorize_files(changed, settings.source_paths)
    changes = [
        SourceChange(
            path=path,
            is_code=_is_code_change(path, range_base, head, cwd),
            commits=_commits_for(path, range_base, head, cwd),
        )
        for path in src_files
    ]
    return decide_tests_first(
        changes, bool(test_files), run_escapes, settings.source_paths
    )


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

    try:
        settings = load_repo_settings(args.repo_root)
    except (OSError, ValueError, TypeError) as exc:
        print(f"FAIL: cannot read .ticket-engine.toml: {exc}")
        return 1

    event_labels, event_texts, event_base = extract_event_info(args.event_path)
    run_escapes = find_escape_reasons(
        texts=list(args.message) + event_texts,
        labels=list(args.label) + event_labels,
        explicit_reason=args.exempt_reason,
    )

    if args.files is not None:
        passed, message = evaluate_tests_first(
            args.files,
            escape_reasons=run_escapes,
            source_paths=settings.source_paths,
        )
    else:
        try:
            passed, message = _git_mode(args, settings, event_base, run_escapes)
        except GitError as exc:
            print(
                f"FAIL: cannot determine the changes to check: {exc}. "
                f"{_FULL_HISTORY_HINT}"
            )
            return 1

    print(message)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
