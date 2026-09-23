"""Pure integrity gate core.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Integrity core) and ADR 0001 establish
that unattended auto-merges require an integrity gate running after target repo
gates. An agent rewarded for a green build could otherwise weaken or mute tests.
The integrity core is pure (no I/O, no network, no clock reads) so every verdict
is testable reproducibly with static snapshots and fixtures.
Slice 1 implements:
- Check 1: no newly skipped or xfailed tests (naming the test).
- Check 2: no deleted test functions and no test file losing assertions (naming what was lost).
- Check 6: ticket must be done with every acceptance box ticked.
- Auto-merge: no producing a merge hold.
"""
from __future__ import annotations

import ast
import logging
import pathlib
import re
from collections.abc import Mapping
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
class IntegrityConfig:
    test_paths: list[str] = field(default_factory=lambda: ["tests"])
    src_paths: list[str] = field(default_factory=lambda: ["src"])


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
        self.filename = filename
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


class IntegrityCore:
    """Pure evaluation core for target repo pull request integrity."""

    def evaluate(
        self,
        base_tree: Mapping[str, str],
        pr_diff: str | Mapping[str, str],
        ticket: Ticket | str | pathlib.Path,
        config: IntegrityConfig | None = None,
        test_results: Any = None,
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

        # 3. Check 6: ticket file done and all acceptance boxes ticked
        ticket_obj, ticket_raw_text = self._resolve_ticket(ticket)
        if not ticket_obj.is_done():
            is_failing = True
            reasons.append(
                f"Check 6 fail: Ticket status is '{ticket_obj.status}', expected 'done'"
            )

        unticked_reasons = self._check_acceptance_criteria(ticket_raw_text)
        if unticked_reasons:
            is_failing = True
            reasons.extend(unticked_reasons)

        # 4. Check test results if provided
        if test_results is not None:
            failed = (
                isinstance(test_results, Mapping) and test_results.get("passed") is False
            ) or (hasattr(test_results, "passed") and not test_results.passed)
            if failed:
                is_failing = True
                reasons.append("Check fail: Test execution results indicate test failure")

        # 5. Check auto-merge flag (ADR 0001 §3)
        if not ticket_obj.auto_merge:
            is_holding = True
            reasons.append(
                "Hold: Ticket has Auto-merge: no set (requires developer approval)"
            )

        if is_failing:
            return IntegrityVerdict(verdict=Verdict.FAIL, reasons=reasons)
        if is_holding:
            return IntegrityVerdict(verdict=Verdict.HOLD, reasons=reasons)

        return IntegrityVerdict(
            verdict=Verdict.PASS,
            reasons=["All integrity checks passed (checks 1, 2, 6)"],
        )

    def _resolve_ticket(
        self, ticket: Ticket | str | pathlib.Path
    ) -> tuple[Ticket, str]:
        if isinstance(ticket, Ticket):
            raw = ticket.raw_text
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
        if not raw_ticket:
            return []

        lines = raw_ticket.splitlines()
        in_ac_section = False
        total_boxes = 0
        unticked_boxes: list[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped.lower().startswith("## acceptance criteria"):
                in_ac_section = True
                continue
            if in_ac_section and stripped.startswith("## ") and not stripped.lower().startswith("## acceptance criteria"):
                break

            if in_ac_section:
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
