"""Command line interface for ticket-engine dispatch.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions and Ticket 01 Acceptance Criterion 6
require `dispatch --dry-run <path-to-clone>` so the developer can inspect the
frontier, planned start actions, skipped windows tickets, and parse findings
against any target repo without starting workers or modifying state.
"""
from __future__ import annotations

import argparse
import logging
import pathlib
import sys

from ticket_engine.dispatch import DispatchCore, StartTicketAction, WorldSnapshot
from ticket_engine.parser import Ticket, TicketParser

logger = logging.getLogger(__name__)


def load_tickets_from_path(repo_path: pathlib.Path) -> list[Ticket]:
    parser = TicketParser()
    tickets: list[Ticket] = []

    # Look for .scratch/*/issues/*.md
    scratch_dir = repo_path / ".scratch"
    pattern = "**/*.md" if not scratch_dir.is_dir() else ".scratch/*/issues/*.md"

    md_files = list(repo_path.glob(pattern))
    for file_path in md_files:
        if file_path.name.lower() in ("spec.md", "readme.md"):
            continue
        # Only parse issue markdown files
        if "issues" in file_path.parts or not scratch_dir.is_dir():
            tickets.append(parser.parse_file(file_path))

    return tickets


def run_dispatch_dry_run(path_str: str, concurrency_limit: int = 2) -> int:
    repo_path = pathlib.Path(path_str).resolve()
    if not repo_path.exists():
        print(f"Error: Path '{path_str}' does not exist.", file=sys.stderr)
        return 1

    tickets = load_tickets_from_path(repo_path)
    core = DispatchCore()
    snapshot = WorldSnapshot(tickets=tickets, concurrency_limit=concurrency_limit)
    result = core.evaluate(snapshot)

    print(f"=== Frontier: ({len(result.frontier)} ticket(s)) ===")
    if result.frontier:
        for t in result.frontier:
            print(f"  - {t.number:02d}: {t.title} [Runner: {t.runner}, Status: {t.status}]")
    else:
        print("  (none)")

    print(f"\n=== Start actions: ({len(result.actions)} action(s)) ===")
    if result.actions:
        for a in result.actions:
            if isinstance(a, StartTicketAction):
                print(f"  - Start {a.ticket.number:02d}: {a.ticket.title} (Runner: {a.runner})")
            else:
                print(f"  - {a}")
    else:
        print("  (none)")

    print(f"\n=== Skipped windows tickets: ({len(result.skipped_windows_tickets)} ticket(s)) ===")
    if result.skipped_windows_tickets:
        for t in result.skipped_windows_tickets:
            print(f"  - {t.number:02d}: {t.title}")
    else:
        print("  (none)")

    print(f"\n=== Parse findings: ({len(result.findings)} finding(s)) ===")
    if result.findings:
        for f in result.findings:
            loc = f" [{f.file}]" if f.file else ""
            print(f"  - {loc} {f.message}")
    else:
        print("  (none)")

    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="ticket-engine dispatcher",
    )
    parser.add_argument(
        "--dry-run",
        dest="dry_run_path",
        metavar="PATH",
        help="Run in dry-run mode against target repo clone at PATH without taking actions",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=None,
        help="Optional target repo clone path",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Maximum tickets in flight per repo (default: 2)",
    )

    args = parser.parse_args(argv)

    target_path = args.dry_run_path or args.path
    if not target_path:
        parser.print_help()
        return 1

    return run_dispatch_dry_run(target_path, concurrency_limit=args.concurrency)


if __name__ == "__main__":
    sys.exit(main())
