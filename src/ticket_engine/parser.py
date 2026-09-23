"""Ticket markdown parser and domain structures.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Ticket parsing) and User Story 2 require
tickets to be machine-readable with identical semantics across all target repos.
Status, Blocked by, Runner, and Auto-merge lines must parse whether formatted
with markdown bold (**Field:**) or plain (Field:).
Legacy words (such as 'complete', 'human-task') are recorded as findings and
never treated as 'done', preventing unverified work from advancing the frontier.
"""
from __future__ import annotations

import logging
import pathlib
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

VALID_STATUSES = {
    "ready-for-agent",
    "ready-for-developer",
    "in-progress",
    "blocked",
    "done",
}

_HEADER_RE = re.compile(r"^#\s*(\d+)\s*:\s*(.+)$", re.MULTILINE)
_STATUS_RE = re.compile(r"^(?:\*\*)?Status:(?:\*\*)?\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_BLOCKED_BY_RE = re.compile(r"^(?:\*\*)?Blocked\s+by:(?:\*\*)?\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_RUNNER_RE = re.compile(r"^(?:\*\*)?Runner:(?:\*\*)?\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_AUTO_MERGE_RE = re.compile(r"^(?:\*\*)?Auto-merge:(?:\*\*)?\s*(.+)$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class ParseFinding:
    message: str
    file: str = ""


@dataclass
class Ticket:
    number: int
    title: str
    slug: str
    status: str
    blocked_by: list[int] = field(default_factory=list)
    runner: str = "any"
    auto_merge: bool = True
    findings: list[ParseFinding] = field(default_factory=list)
    path: pathlib.Path | None = None

    def is_done(self) -> bool:
        if self.status != "done":
            return False
        # If there are status findings (e.g. unknown or legacy status), never treat as done
        for finding in self.findings:
            if "status" in finding.message.lower():
                return False
        return True


class TicketParser:
    """Parses ticket markdown files into Ticket domain models."""

    def parse_file(self, path: pathlib.Path | str) -> Ticket:
        p = pathlib.Path(path)
        content = p.read_text(encoding="utf-8")
        return self.parse_text(content, filename=p.name, path=p)

    def parse_text(
        self,
        content: str,
        filename: str = "",
        path: pathlib.Path | None = None,
    ) -> Ticket:
        findings: list[ParseFinding] = []

        # 1. Parse header (number, title, slug)
        header_match = _HEADER_RE.search(content)
        if header_match:
            number = int(header_match.group(1))
            title = header_match.group(2).strip()
        else:
            # Fallback to filename prefix if present
            fn_match = re.match(r"^(\d+)-?(.*)\.md$", filename)
            if fn_match:
                number = int(fn_match.group(1))
                title = fn_match.group(2).replace("-", " ").strip()
            else:
                number = 0
                title = filename

        # Compute slug
        fn_match = re.match(r"^\d+-(.+)\.md$", filename)
        if fn_match:
            slug = fn_match.group(1).strip()
        else:
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", title.lower()).strip("-")

        # 2. Parse Status
        status_match = _STATUS_RE.search(content)
        if status_match:
            raw_status = status_match.group(1).strip()
            # Clean possible markdown bold artifacts if trailing
            raw_status = raw_status.strip("*_").strip()
            status = raw_status.lower()
            if status not in VALID_STATUSES:
                finding = ParseFinding(
                    message=f"Unknown or legacy status '{raw_status}'",
                    file=filename,
                )
                findings.append(finding)
        else:
            status = "unknown"
            findings.append(ParseFinding(message="Missing Status line", file=filename))

        # 3. Parse Blocked by
        blocked_by: list[int] = []
        blocked_match = _BLOCKED_BY_RE.search(content)
        if blocked_match:
            raw_blocked = blocked_match.group(1).strip()
            raw_blocked = raw_blocked.strip("*_").strip()
            if not raw_blocked.lower().startswith("none"):
                # Extract all numbers from the line
                nums = re.findall(r"\b\d+\b", raw_blocked)
                for num_str in nums:
                    blocked_by.append(int(num_str))

        # 4. Parse Runner (missing defaults to "any")
        runner_match = _RUNNER_RE.search(content)
        if runner_match:
            raw_runner = runner_match.group(1).strip().strip("*_").strip().lower()
            runner = raw_runner if raw_runner in ("any", "windows") else "any"
        else:
            runner = "any"

        # 5. Parse Auto-merge (missing defaults to True / "yes")
        auto_merge_match = _AUTO_MERGE_RE.search(content)
        if auto_merge_match:
            raw_auto = auto_merge_match.group(1).strip().strip("*_").strip().lower()
            auto_merge = raw_auto not in ("no", "false")
        else:
            auto_merge = True

        return Ticket(
            number=number,
            title=title,
            slug=slug,
            status=status,
            blocked_by=blocked_by,
            runner=runner,
            auto_merge=auto_merge,
            findings=findings,
            path=path,
        )
