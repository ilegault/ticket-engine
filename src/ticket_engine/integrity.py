"""Pure integrity gate core.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Integrity core) and ADR 0001 establish
that unattended auto-merges require an integrity gate running after target repo
gates. An agent rewarded for a green build could otherwise weaken or mute tests.
The integrity core is pure (no I/O, no network, no clock reads) so every verdict
is testable reproducibly with static snapshots and fixtures.

Checks implemented:
- Check 1: no newly skipped or xfailed tests (naming the test) (ADR 0001 §2.1).
- Check 2: no deleted test functions and no test file losing assertions (naming what was lost) (ADR 0001 §2.2).
- Check 3: any ratchet file with a higher value than on base -> fail (ADR 0001 §2.3).
- Check 4: PR touching .github/, gate scripts, docs/adr/, AGENTS.md, CONTEXT.md -> hold, naming paths (ADR 0001 §2.4).
- Check 5: tests-first escape hatch used (label, commit/PR tag) -> hold (ADR 0001 §2.5).
- Check 6: ticket must be done with every acceptance box ticked (ADR 0001 §2.6).
- Check 7: new test functions run against base source must fail on base; any new test passing on base -> hold, listing the tests (ADR 0001 §2.7). Silent if no new tests.
- Auto-merge: no producing a merge hold regardless of other checks (ADR 0001 §3).
- Verdict precedence: when both fail and hold reasons exist, verdict is fail (ADR 0001).
"""
from __future__ import annotations

import ast
import json
import logging
import pathlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ticket_engine.parser import Ticket, TicketParser

logger = logging.getLogger(__name__)


class Verdict(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    HOLD = "hold"


@dataclass(frozen=True)
class IntegrityVerdict:
    verdict: Verdict
    reasons: list[str] = field(default_factory=list)

    def is_pass(self) -> bool:
        return self.verdict == Verdict.PASS

    def is_fail(self) -> bool:
        return self.verdict == Verdict.FAIL

    def is_hold(self) -> bool:
        return self.verdict == Verdict.HOLD


@dataclass(frozen=True)
class BaseTestResults:
    new_tests: list[str] = field(default_factory=list)
    passed_tests: list[str] = field(default_factory=list)
    failed_tests: list[str] = field(default_factory=list)
    runtime_seconds: float = 0.0
    # New tests that did not pass on the PR's own code in the gate environment, so
    # their result on base proves nothing (missing package, missing env var, ...).
    unrunnable_tests: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class IntegrityConfig:
    test_paths: list[str] = field(default_factory=lambda: ["tests"])
    src_paths: list[str] = field(default_factory=lambda: ["src"])
    ratchet_files: list[str] = field(default_factory=list)
    gate_paths: list[str] = field(
        default_factory=lambda: ["scripts/check_tests_first.py"]
    )


@dataclass(frozen=True)
class _TestFunctionInfo:
    qualified_name: str
    func_name: str
    file_path: str
    is_skipped: bool
    skip_reason: str = ""


_DIFF_FILE_HEADER_RE = re.compile(
    r"^diff --git a/(?P<old>.+?) b/(?P<new>.+?)$", re.MULTILINE
)
_HUNK_HEADER_RE = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_ACCEPTANCE_BOX_RE = re.compile(r"^[-*]\s*\[([ xX])\]\s*(.+)$")
_ESCAPE_TAG_RE = re.compile(
    r"\[(?:no-test-needed|tests-exempt|skip-test-gate)(?::\s*([^\]]+))?\]",
    re.IGNORECASE,
)
_EXEMPT_LABELS = {"tests-exempt", "skip-test-gate", "no-test-needed"}


def parse_ratchet_value(content: str | None) -> float | int | dict[str, float | int] | None:
    """Parse ratchet values from content.

    Supports:
    - Pure numbers (integer or float)
    - Key-value lines (key: value or key=value) or JSON objects
    - Non-empty line counts for list baselines
    """
    if content is None:
        return None
    stripped = content.strip()
    if not stripped:
        return 0

    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                return {k: float(v) for k, v in data.items() if isinstance(v, (int, float))}
        except json.JSONDecodeError:
            pass

    try:
        val = float(stripped)
        return int(val) if val.is_integer() else val
    except ValueError:
        pass

    kv: dict[str, float | int] = {}
    is_kv = True
    for line in stripped.splitlines():
        l_str = line.strip()
        if not l_str or l_str.startswith("#"):
            continue
        if ":" in l_str or "=" in l_str:
            sep = ":" if ":" in l_str else "="
            k, v = l_str.split(sep, 1)
            try:
                num = float(v.strip())
                kv[k.strip()] = int(num) if num.is_integer() else num
            except ValueError:
                is_kv = False
                break
        else:
            is_kv = False
            break

    if is_kv and kv:
        return kv

    return len([line for line in stripped.splitlines() if line.strip()])



_TICKET_FILE_RE = re.compile(r"^\.scratch/[^/]+/issues/[^/]+\.md$")


def get_diff_changed_paths(
    pr_diff: str | Mapping[str, str], base_tree: Mapping[str, str]
) -> set[str]:
    """Extract all file paths touched by the PR."""
    changed_paths: set[str] = set()
    if isinstance(pr_diff, Mapping):
        for p, content in pr_diff.items():
            if p not in base_tree or base_tree[p] != content:
                changed_paths.add(p.replace("\\", "/"))
        for p in base_tree:
            if p not in pr_diff:
                changed_paths.add(p.replace("\\", "/"))
        return changed_paths

    for line in pr_diff.splitlines():
        if line.startswith("diff --git "):
            m = _DIFF_FILE_HEADER_RE.match(line)
            if m:
                old_p = m.group("old").strip()
                new_p = m.group("new").strip()
                if old_p and old_p != "/dev/null":
                    changed_paths.add(old_p.replace("\\", "/"))
                if new_p and new_p != "/dev/null":
                    changed_paths.add(new_p.replace("\\", "/"))
        elif line.startswith("--- "):
            p = line[4:].strip().removeprefix("a/")
            if p and p != "/dev/null":
                changed_paths.add(p.replace("\\", "/"))
        elif line.startswith("+++ "):
            p = line[4:].strip().removeprefix("b/")
            if p and p != "/dev/null":
                changed_paths.add(p.replace("\\", "/"))
    return changed_paths


def get_diff_added_lines_with_locations(
    pr_diff: str | Mapping[str, str], base_tree: Mapping[str, str]
) -> list[tuple[str, int, str]]:
    """Return list of (file_path, line_number, added_line_text)."""
    added_lines: list[tuple[str, int, str]] = []
    if isinstance(pr_diff, Mapping):
        import difflib

        for p, content in pr_diff.items():
            norm_p = p.replace("\\", "/")
            base_content = base_tree.get(p)
            if base_content is None:
                for idx, line in enumerate(content.splitlines(), start=1):
                    added_lines.append((norm_p, idx, line))
            elif base_content != content:
                cur_line = 0
                diff = difflib.unified_diff(
                    base_content.splitlines(keepends=True),
                    content.splitlines(keepends=True),
                )
                for dl in diff:
                    m = _HUNK_HEADER_RE.match(dl)
                    if m:
                        cur_line = int(m.group("new_start"))
                    elif dl.startswith("+") and not dl.startswith("+++"):
                        added_lines.append((norm_p, cur_line, dl[1:].rstrip("\r\n")))
                        cur_line += 1
                    elif dl.startswith(" "):
                        cur_line += 1
        return added_lines

    # Unified diff string
    file_chunks: list[str] = []
    lines = pr_diff.splitlines(keepends=True)
    current_chunk: list[str] = []
    for line in lines:
        if line.startswith("diff --git ") and current_chunk:
            file_chunks.append("".join(current_chunk))
            current_chunk = [line]
        else:
            current_chunk.append(line)
    if current_chunk:
        file_chunks.append("".join(current_chunk))

    for chunk in file_chunks:
        chunk_lines = chunk.splitlines()
        target_path: str | None = None
        for cl in chunk_lines:
            if cl.startswith("+++ "):
                p = cl[4:].strip().removeprefix("b/")
                if p != "/dev/null":
                    target_path = p.replace("\\", "/")
            elif cl.startswith("--- ") and not target_path:
                p = cl[4:].strip().removeprefix("a/")
                if p != "/dev/null":
                    target_path = p.replace("\\", "/")
        if not target_path:
            continue

        cur_new_line = 0
        for cl in chunk_lines:
            if cl.startswith("@@ "):
                m = _HUNK_HEADER_RE.match(cl)
                if m:
                    cur_new_line = int(m.group("new_start"))
            elif cl.startswith("+") and not cl.startswith("+++"):
                added_lines.append((target_path, cur_new_line, cl[1:]))
                cur_new_line += 1
            elif cl.startswith(" "):
                cur_new_line += 1

    return added_lines


def is_protected_path(path: str, gate_paths: Sequence[str]) -> bool:
    """Check if a path is protected by governance rules (ADR 0001 §2.4)."""
    norm = path.replace("\\", "/").strip().lstrip("/")
    if norm.startswith(".github/") or norm == ".github":
        return True
    if norm.startswith("docs/adr/") or norm == "docs/adr":
        return True
    if norm == "AGENTS.md" or norm.endswith("/AGENTS.md"):
        return True
    if norm == "CONTEXT.md" or norm.endswith("/CONTEXT.md"):
        return True
    for gp in gate_paths:
        gp_norm = gp.replace("\\", "/").strip().lstrip("/")
        if norm == gp_norm or norm.startswith(gp_norm.rstrip("/") + "/"):
            return True
    return False



def parse_and_apply_diff(
    base_tree: Mapping[str, str], pr_diff: str
) -> dict[str, str]:
    """Purely reconstruct head tree from base tree and unified git diff."""
    if not pr_diff or not pr_diff.strip():
        return dict(base_tree)

    head_tree = dict(base_tree)

    # Split diff into per-file chunks
    file_chunks: list[str] = []
    lines = pr_diff.splitlines(keepends=True)
    current_chunk: list[str] = []

    for line in lines:
        if line.startswith("diff --git ") and current_chunk:
            file_chunks.append("".join(current_chunk))
            current_chunk = [line]
        else:
            current_chunk.append(line)
    if current_chunk:
        file_chunks.append("".join(current_chunk))

    for chunk in file_chunks:
        chunk_lines = chunk.splitlines()
        old_path: str | None = None
        new_path: str | None = None
        is_deleted = False

        for cl in chunk_lines:
            if cl.startswith("--- "):
                p = cl[4:].strip()
                old_path = p.removeprefix("a/")
            elif cl.startswith("+++ "):
                p = cl[4:].strip()
                new_path = p.removeprefix("b/")
            elif cl.startswith("deleted file mode"):
                is_deleted = True

        if old_path == "/dev/null":
            old_path = None
        if new_path == "/dev/null":
            new_path = None
            is_deleted = True

        if is_deleted:
            if old_path and old_path in head_tree:
                del head_tree[old_path]
            continue

        target_path = new_path or old_path
        if not target_path:
            continue

        base_content = base_tree.get(old_path, "") if old_path else ""
        base_lines = base_content.splitlines()

        # Parse hunks
        hunk_lines_groups: list[list[str]] = []
        current_hunk: list[str] = []
        for cl in chunk_lines:
            if cl.startswith("@@ "):
                if current_hunk:
                    hunk_lines_groups.append(current_hunk)
                current_hunk = [cl]
            elif current_hunk:
                current_hunk.append(cl)
        if current_hunk:
            hunk_lines_groups.append(current_hunk)

        if not hunk_lines_groups:
            continue

        new_lines: list[str] = []
        base_idx = 0

        for hgroup in hunk_lines_groups:
            header = hgroup[0]
            m = _HUNK_HEADER_RE.match(header)
            if not m:
                continue
            old_start = int(m.group("old_start"))
            # In diffs for new files, old_start is 0
            target_base_idx = max(0, old_start - 1)

            while base_idx < target_base_idx and base_idx < len(base_lines):
                new_lines.append(base_lines[base_idx])
                base_idx += 1

            for hline in hgroup[1:]:
                if hline.startswith(" "):
                    if base_idx < len(base_lines):
                        new_lines.append(base_lines[base_idx])
                        base_idx += 1
                    else:
                        new_lines.append(hline[1:])
                elif hline.startswith("-"):
                    base_idx += 1
                elif hline.startswith("+"):
                    new_lines.append(hline[1:])
                elif hline.startswith(r"\ No newline"):
                    pass

        while base_idx < len(base_lines):
            new_lines.append(base_lines[base_idx])
            base_idx += 1

        head_content = "\n".join(new_lines)
        if new_lines and not head_content.endswith("\n"):
            head_content += "\n"
        head_tree[target_path] = head_content

    return head_tree


class _ASTVisitor:
    """Extracts test functions, skip status, and counts assertions in Python AST."""

    def __init__(self, filename: str, content: str):
        self.filename = filename.replace("\\", "/")
        self.content = content
        self.tests: dict[str, _TestFunctionInfo] = {}
        self.assertion_count = 0
        self._parsed = False

    def parse(self) -> None:
        try:
            tree = ast.parse(self.content, filename=self.filename)
        except SyntaxError:
            return

        self._parsed = True
        module_skipped = self._has_module_level_skip(tree)

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if self._is_test_func_name(node.name):
                    qname = f"{self.filename}::{node.name}"
                    is_skip, reason = self._check_func_skip(node, module_skipped)
                    self.tests[qname] = _TestFunctionInfo(
                        qualified_name=qname,
                        func_name=node.name,
                        file_path=self.filename,
                        is_skipped=is_skip,
                        skip_reason=reason,
                    )
            elif isinstance(node, ast.ClassDef):
                class_skipped = module_skipped or self._has_skip_decorator(node.decorator_list)
                for class_node in node.body:
                    if (
                        isinstance(class_node, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and self._is_test_func_name(class_node.name)
                    ):
                        qname = f"{self.filename}::{node.name}::{class_node.name}"
                        is_skip, reason = self._check_func_skip(class_node, class_skipped)
                        self.tests[qname] = _TestFunctionInfo(
                            qualified_name=qname,
                            func_name=class_node.name,
                            file_path=self.filename,
                            is_skipped=is_skip,
                            skip_reason=reason,
                        )

        self.assertion_count = self._count_assertions(tree)

    def _is_test_func_name(self, name: str) -> bool:
        return name.startswith("test_") or name.endswith("_test")

    def _has_module_level_skip(self, tree: ast.Module) -> bool:
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Name)
                        and target.id == "pytestmark"
                        and self._expr_indicates_skip(node.value)
                    ):
                        return True
            elif (
                isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call)
                and self._call_indicates_skip(node.value)
            ):
                return True
        return False

    def _has_skip_decorator(self, decorators: list[ast.expr]) -> bool:
        for dec in decorators:
            if self._expr_indicates_skip(dec):
                return True
        return False

    def _check_func_skip(
        self, func_node: ast.FunctionDef | ast.AsyncFunctionDef, outer_skipped: bool
    ) -> tuple[bool, str]:
        if outer_skipped:
            return True, "Enclosing module or class marked skip"

        for dec in func_node.decorator_list:
            if self._expr_indicates_skip(dec):
                return True, "Decorated with skip or xfail"

        for sub_node in ast.walk(func_node):
            if isinstance(sub_node, ast.Call) and self._call_indicates_skip(sub_node):
                return True, "Calls skip or xfail in function body"

        return False, ""

    def _expr_indicates_skip(self, node: ast.expr) -> bool:
        if isinstance(node, ast.Call):
            return self._expr_indicates_skip(node.func)
        if isinstance(node, ast.Attribute):
            attr = node.attr.lower()
            if attr in ("skip", "skipif", "xfail", "expectedfailure"):
                return True
            return self._expr_indicates_skip(node.value)
        if isinstance(node, ast.Name):
            return node.id.lower() in ("skip", "skipif", "xfail", "expectedfailure")
        if isinstance(node, (ast.List, ast.Tuple)):
            return any(self._expr_indicates_skip(el) for el in node.elts)
        return False

    def _call_indicates_skip(self, call: ast.Call) -> bool:
        fn = call.func
        if isinstance(fn, ast.Attribute):
            attr = fn.attr.lower()
            if attr in ("skip", "skipif", "xfail", "skiptest"):
                return True
        elif isinstance(fn, ast.Name):
            if fn.id.lower() in ("skip", "skipif", "xfail", "skiptest"):
                return True
        return False

    def _count_assertions(self, tree: ast.AST) -> int:
        count = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.Assert):
                count += 1
            elif isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Attribute):
                    attr = fn.attr
                    if attr.startswith("assert") or attr in ("raises", "warns", "approx"):
                        count += 1
                elif isinstance(fn, ast.Name):
                    if fn.id.startswith("assert_") or fn.id == "assert_that":
                        count += 1
        return count


def find_new_test_functions(
    base_tree: Mapping[str, str],
    pr_diff: str | Mapping[str, str],
    test_paths: Sequence[str] = ("tests",),
) -> list[str]:
    """Identify test functions present in head that are new (not merely edited).

    Uses AST visitor on test files from base_tree and reconstructed head_tree.
    Returns qualified test names (e.g. 'tests/test_x.py::test_fn') not present in base.
    """
    if isinstance(pr_diff, Mapping):
        head_tree = dict(pr_diff)
    else:
        head_tree = parse_and_apply_diff(base_tree, pr_diff)

    test_dirs = tuple(p.replace("\\", "/").strip().lstrip("/").rstrip("/") + "/" for p in test_paths)

    def is_test_file(path: str) -> bool:
        norm = path.replace("\\", "/").strip().lstrip("/")
        return norm.endswith(".py") and any(norm.startswith(td) for td in test_dirs)

    base_test_files = {p.replace("\\", "/"): c for p, c in base_tree.items() if is_test_file(p)}
    head_test_files = {p.replace("\\", "/"): c for p, c in head_tree.items() if is_test_file(p)}

    base_visitors = {p: _ASTVisitor(p, c) for p, c in base_test_files.items()}
    for v in base_visitors.values():
        v.parse()

    head_visitors = {p: _ASTVisitor(p, c) for p, c in head_test_files.items()}
    for v in head_visitors.values():
        v.parse()

    base_tests: dict[str, _TestFunctionInfo] = {}
    for v in base_visitors.values():
        base_tests.update(v.tests)

    new_tests: list[str] = []
    for v in head_visitors.values():
        for qname in v.tests:
            if qname not in base_tests:
                new_tests.append(qname)

    return new_tests


class IntegrityCore:
    """Pure evaluation core for target repo pull request integrity."""

    def evaluate(
        self,
        base_tree: Mapping[str, str],
        pr_diff: str | Mapping[str, str],
        ticket: Ticket | str | pathlib.Path,
        config: IntegrityConfig | None = None,
        test_results: Any = None,
        labels: Sequence[str] | None = None,
        commit_messages: Sequence[str] | None = None,
        pr_text: str | None = None,
        base_test_results: BaseTestResults | Mapping[str, Any] | None = None,
    ) -> IntegrityVerdict:
        cfg = config or IntegrityConfig()
        reasons: list[str] = []
        is_failing = False
        is_holding = False

        # 1. Resolve head tree
        if isinstance(pr_diff, Mapping):
            head_tree = dict(pr_diff)
        else:
            head_tree = parse_and_apply_diff(base_tree, pr_diff)

        # 2. Check 1 & Check 2: Test functions, skips, assertions
        test_dirs = tuple(p.replace("\\", "/").rstrip("/") + "/" for p in cfg.test_paths)

        def is_test_file(path: str) -> bool:
            norm = path.replace("\\", "/")
            return norm.endswith(".py") and any(norm.startswith(td) for td in test_dirs)

        base_test_files = {p: c for p, c in base_tree.items() if is_test_file(p)}
        head_test_files = {p: c for p, c in head_tree.items() if is_test_file(p)}

        base_visitors = {p: _ASTVisitor(p, c) for p, c in base_test_files.items()}
        for v in base_visitors.values():
            v.parse()

        head_visitors = {p: _ASTVisitor(p, c) for p, c in head_test_files.items()}
        for v in head_visitors.values():
            v.parse()

        base_tests: dict[str, _TestFunctionInfo] = {}
        for v in base_visitors.values():
            base_tests.update(v.tests)

        head_tests: dict[str, _TestFunctionInfo] = {}
        for v in head_visitors.values():
            head_tests.update(v.tests)

        # Check 1: newly skipped or xfailed tests
        for qname, h_info in head_tests.items():
            if h_info.is_skipped:
                b_info = base_tests.get(qname)
                # If test wasn't in base or was not skipped in base, it's newly skipped
                if b_info is None or not b_info.is_skipped:
                    is_failing = True
                    reasons.append(
                        f"Check 1 fail: Test '{h_info.func_name}' in {h_info.file_path} is newly skipped or marked expected-to-fail"
                    )

        # Check 2: deleted test function or test file losing assertions
        for qname, b_info in base_tests.items():
            if qname not in head_tests:
                is_failing = True
                reasons.append(
                    f"Check 2 fail: Deleted test function '{b_info.func_name}' in {b_info.file_path}"
                )

        for path, b_visitor in base_visitors.items():
            h_visitor = head_visitors.get(path)
            h_count = h_visitor.assertion_count if h_visitor else 0
            if h_count < b_visitor.assertion_count:
                lost = b_visitor.assertion_count - h_count
                is_failing = True
                reasons.append(
                    f"Check 2 fail: Test file '{path}' has fewer assertions than on base (lost {lost} assertion(s): {h_count} < {b_visitor.assertion_count})"
                )

        # 3. Check 3: ratchet files
        for rpath in cfg.ratchet_files:
            rpath_norm = rpath.replace("\\", "/")
            head_content = head_tree.get(rpath_norm)
            base_content = base_tree.get(rpath_norm)
            if head_content is not None:
                h_val = parse_ratchet_value(head_content)
                b_val = parse_ratchet_value(base_content) if base_content is not None else 0
                if isinstance(h_val, (int, float)) and isinstance(b_val, (int, float)):
                    if h_val > b_val:
                        is_failing = True
                        reasons.append(
                            f"Check 3 fail: Ratchet file '{rpath_norm}' has higher value than on base ({h_val} > {b_val})"
                        )
                elif isinstance(h_val, dict) and isinstance(b_val, dict):
                    for k, h_num in h_val.items():
                        b_num = b_val.get(k, 0)
                        if h_num > b_num:
                            is_failing = True
                            reasons.append(
                                f"Check 3 fail: Ratchet file '{rpath_norm}' metric '{k}' increased from {b_num} to {h_num}"
                            )
                elif isinstance(h_val, (int, float)) and b_val is None and h_val > 0:
                    is_failing = True
                    reasons.append(
                        f"Check 3 fail: Ratchet file '{rpath_norm}' has higher value than on base ({h_val} > 0)"
                    )

        # 4. Check 4: protected governance or gate paths
        changed_paths = get_diff_changed_paths(pr_diff, base_tree)
        protected_touched = [
            p for p in sorted(changed_paths) if is_protected_path(p, cfg.gate_paths)
        ]
        if protected_touched:
            is_holding = True
            reasons.append(
                f"Check 4 hold: PR modifies protected governance or gate path(s): {', '.join(protected_touched)}"
            )

        # 5. Check 5: tests-first escape hatch used
        escape_reasons: list[str] = []
        if labels:
            for lbl in labels:
                if lbl.strip().lower() in _EXEMPT_LABELS:
                    escape_reasons.append(f"PR label '{lbl.strip()}'")

        texts_to_scan = list(commit_messages or [])
        if pr_text:
            texts_to_scan.append(pr_text)
        for text in texts_to_scan:
            for match in _ESCAPE_TAG_RE.finditer(text):
                tag_reason = match.group(1)
                full_tag = match.group(0)
                if tag_reason and tag_reason.strip():
                    escape_reasons.append(
                        f"Annotation {full_tag} with reason: '{tag_reason.strip()}'"
                    )
                else:
                    escape_reasons.append(f"Annotation {full_tag}")

        if escape_reasons:
            is_holding = True
            reasons.append(
                f"Check 5 hold: Tests-first escape hatch used ({', '.join(escape_reasons)})"
            )

        # 6. Check 6: ticket file done and all acceptance boxes ticked.
        # A PR that changes no ticket file has nothing to verify; it must fail rather
        # than be checked against some other ticket (that let one merge unfinished).
        ticket_obj, ticket_raw_text = self._resolve_ticket(ticket)
        changed_tickets = sorted(p for p in changed_paths if _TICKET_FILE_RE.match(p))
        if len(changed_tickets) > 1:
            # A worker PR carries exactly one ticket. Several changed ticket files mean a
            # planning PR (a new or rewritten ticket set), which has no single ticket to
            # judge and must be merged by a human. Judging the first file found used to
            # fail every planning PR on some unrelated ticket's status.
            is_holding = True
            reasons.append(
                f"Check 6 hold: the PR changes {len(changed_tickets)} ticket files, so it is a "
                "planning PR; a human must review and merge it"
            )
        elif not ticket_raw_text.strip():
            is_failing = True
            reasons.append(
                "Check 6 fail: the PR does not change any ticket file under "
                ".scratch/<effort>/issues/; set its ticket's Status: done and tick every "
                "acceptance criterion in this PR"
            )
        else:
            if not ticket_obj.is_done():
                is_failing = True
                reasons.append(
                    f"Check 6 fail: Ticket status is '{ticket_obj.status}', expected 'done'"
                )

            unticked_reasons = self._check_acceptance_criteria(ticket_raw_text)
            if unticked_reasons:
                is_failing = True
                reasons.extend(unticked_reasons)

            # A ticket's work is proven by tests in the same PR (ADR 0001). A PR that
            # marks its ticket done while changing no test file shipped nothing
            # provable: once, a worker reverted its whole implementation and only
            # ticked the boxes, and that PR merged as "done". Some tickets legitimately
            # have no tests (CI or docs work), so this holds for a human rather than
            # failing: it can never auto-merge, and a real infra ticket isn't punished.
            if ticket_obj.is_done():
                test_prefixes = tuple(tp.rstrip("/") + "/" for tp in cfg.test_paths)
                if not any(p.startswith(test_prefixes) for p in changed_paths):
                    is_holding = True
                    reasons.append(
                        "Check 6 hold: the PR marks its ticket done but changes no test file; "
                        "a human must confirm the work actually shipped"
                    )

        # 7. Check test results if provided
        if test_results is not None:
            failed = (
                isinstance(test_results, Mapping) and test_results.get("passed") is False
            ) or (hasattr(test_results, "passed") and not test_results.passed)
            if failed:
                is_failing = True
                reasons.append("Check fail: Test execution results indicate test failure")

        # 9. Check auto-merge flag (ADR 0001 §3)
        if not ticket_obj.auto_merge:
            is_holding = True
            reasons.append(
                "Hold: Ticket has Auto-merge: no set (requires developer approval)"
            )

        # 10. Check 7: New tests must fail on base code (ADR 0001 §2.7, Ticket 05)
        if base_test_results is not None:
            if isinstance(base_test_results, Mapping):
                new_tests = list(base_test_results.get("new_tests", []))
                passed_on_base = list(base_test_results.get("passed_tests", []))
                failed_on_base = list(base_test_results.get("failed_tests", []))
                unrunnable = list(base_test_results.get("unrunnable_tests", []))
                rt = float(base_test_results.get("runtime_seconds", 0.0))
            else:
                new_tests = list(getattr(base_test_results, "new_tests", []))
                passed_on_base = list(getattr(base_test_results, "passed_tests", []))
                failed_on_base = list(getattr(base_test_results, "failed_tests", []))
                unrunnable = list(getattr(base_test_results, "unrunnable_tests", []))
                rt = float(getattr(base_test_results, "runtime_seconds", 0.0))

            if unrunnable:
                is_holding = True
                reasons.append(
                    "Check 7 hold: new test(s) did not pass on the PR's own code in the gate "
                    "environment, so their result on base proves nothing (missing package or "
                    f"env var?): {', '.join(unrunnable)}"
                )
            if new_tests and not unrunnable:
                if passed_on_base:
                    is_holding = True
                    tests_str = ", ".join(passed_on_base)
                    rt_str = f" (runtime: {rt:.2f}s)" if rt > 0 else ""
                    reasons.append(
                        f"Check 7 hold: New test(s) passed on base code: {tests_str}{rt_str}"
                    )
                else:
                    rt_str = f" (runtime {rt:.2f}s)" if rt > 0 else ""
                    reasons.append(
                        f"Check 7 pass: All {len(failed_on_base or new_tests)} new test(s) failed on base code{rt_str}"
                    )

        if is_failing:
            return IntegrityVerdict(verdict=Verdict.FAIL, reasons=reasons)
        if is_holding:
            return IntegrityVerdict(verdict=Verdict.HOLD, reasons=reasons)

        pass_reasons = ["All integrity checks passed (checks 1-7)"]
        for r in reasons:
            if r.startswith("Check 7 pass"):
                pass_reasons.append(r)

        return IntegrityVerdict(
            verdict=Verdict.PASS,
            reasons=pass_reasons,
        )

    def _resolve_ticket(
        self, ticket: Ticket | str | pathlib.Path
    ) -> tuple[Ticket, str]:
        if isinstance(ticket, Ticket):
            raw = getattr(ticket, "raw_text", "")
            if not raw and ticket.path and ticket.path.is_file():
                raw = ticket.path.read_text(encoding="utf-8")
            return ticket, raw
        if isinstance(ticket, pathlib.Path) or (isinstance(ticket, str) and "\n" not in ticket and pathlib.Path(ticket).is_file()):
            p = pathlib.Path(ticket)
            raw = p.read_text(encoding="utf-8")
            return TicketParser().parse_text(raw, filename=p.name, path=p), raw
        raw = str(ticket)
        return TicketParser().parse_text(raw, filename="ticket.md"), raw

    def _check_acceptance_criteria(self, raw_ticket: str) -> list[str]:
        """Collect the ticket's checkboxes and report unticked ones.

        With a `## Acceptance criteria` heading, only boxes under it count. Without
        one (the /to-tickets template puts boxes straight under "What to build"),
        every box before `## Comments` counts: requiring the heading made the gate
        report "no acceptance criteria" for every such ticket, which no honest PR
        could fix. Boxes under `## Comments` are notes, never criteria.
        """
        if not raw_ticket:
            return []

        lines = raw_ticket.splitlines()
        has_ac_heading = any(
            ln.strip().lower().startswith("## acceptance criteria") for ln in lines
        )
        in_scope = not has_ac_heading
        total_boxes = 0
        unticked_boxes: list[str] = []

        for line in lines:
            stripped = line.strip()
            lowered = stripped.lower()
            if lowered.startswith("## acceptance criteria"):
                in_scope = True
                continue
            if stripped.startswith("## "):
                if has_ac_heading and in_scope:
                    break
                if not has_ac_heading and lowered.startswith("## comments"):
                    break
                continue

            if in_scope:
                m = _ACCEPTANCE_BOX_RE.match(stripped)
                if m:
                    total_boxes += 1
                    checked = m.group(1).strip()
                    item_text = m.group(2).strip()
                    if not checked or checked.lower() != "x":
                        unticked_boxes.append(item_text)

        reasons = []
        if total_boxes == 0:
            reasons.append("Check 6 fail: Ticket has no acceptance criteria checkboxes defined")
        elif unticked_boxes:
            for item in unticked_boxes:
                reasons.append(f"Check 6 fail: Ticket acceptance criterion not ticked: '{item}'")

        return reasons
