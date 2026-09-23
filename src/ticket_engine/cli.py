"""Command line interface for ticket-engine dispatch.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions and Ticket 01/06 Acceptance Criteria
require:
1. `dispatch --dry-run <path-to-clone>`: dry-run mode inspecting frontier, actions,
   skipped windows tickets, and parse findings without taking live actions.
2. `dispatch [path]`: live dispatch mode connecting GitHub and Jules APIs using
   `PIPELINE_TOKEN` and `JULES_API_KEY` to claim frontier tickets and start Jules sessions.
3. Privacy invariant (ADR 0002): Never prints secrets or prompts to stdout/stderr.
"""
from __future__ import annotations

import argparse
import logging
import os
import pathlib
import sys

from ticket_engine.config import load_repo_config
from ticket_engine.dispatch import DispatchCore, StartTicketAction, WorldSnapshot
from ticket_engine.github import GitHubClient
from ticket_engine.jules import JulesClient
from ticket_engine.live_dispatch import LiveDispatcher
from ticket_engine.parser import Ticket, TicketParser

logger = logging.getLogger(__name__)


def load_tickets_from_path(repo_path: pathlib.Path) -> list[Ticket]:
    parser = TicketParser()
    tickets: list[Ticket] = []

    scratch_dir = repo_path / ".scratch"
    pattern = "**/*.md" if not scratch_dir.is_dir() else ".scratch/*/issues/*.md"

    md_files = list(repo_path.glob(pattern))
    for file_path in md_files:
        if file_path.name.lower() in ("spec.md", "readme.md"):
            continue
        if "issues" in file_path.parts or not scratch_dir.is_dir():
            tickets.append(parser.parse_file(file_path))

    return tickets


def run_dispatch_dry_run(path_str: str, concurrency_limit: int = 2) -> int:
    repo_path = pathlib.Path(path_str).resolve()
    if not repo_path.exists():
        print(f"Error: Path '{path_str}' does not exist.", file=sys.stderr)
        return 1

    tickets = load_tickets_from_path(repo_path)
    config = load_repo_config(repo_path)
    core = DispatchCore()
    snapshot = WorldSnapshot(
        tickets=tickets,
        config=config,
        concurrency_limit=concurrency_limit,
    )
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


def run_live_dispatch_cli(
    path_str: str,
    repo: str,
    pipeline_token: str,
    jules_api_key: str,
    paused: bool = False,
) -> int:
    repo_path = pathlib.Path(path_str).resolve()
    if not repo_path.exists():
        print(f"Error: Path '{path_str}' does not exist.", file=sys.stderr)
        return 1

    tickets = load_tickets_from_path(repo_path)
    config = load_repo_config(repo_path)

    github_client = GitHubClient(token=pipeline_token)
    jules_client = JulesClient(api_key=jules_api_key)

    dispatcher = LiveDispatcher(
        repo=repo,
        github_client=github_client,
        jules_client=jules_client,
        config=config,
        paused=paused,
    )

    started = dispatcher.dispatch(tickets=tickets)
    print(f"Dispatched {len(started)} ticket(s) to Jules workers.")
    for t in started:
        print(f"  - Started ticket {t.number:02d}: {t.title}")
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
        default=".",
        help="Target repo clone path (default: current directory)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="Maximum tickets in flight per repo (default: 2)",
    )
    parser.add_argument(
        "--repo",
        default=os.environ.get("GITHUB_REPOSITORY"),
        help="Target repository in owner/repo format (or GITHUB_REPOSITORY env var)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("PIPELINE_TOKEN"),
        help="GitHub pipeline token (or PIPELINE_TOKEN env var)",
    )
    parser.add_argument(
        "--jules-key",
        default=os.environ.get("JULES_API_KEY"),
        help="Jules API key (or JULES_API_KEY env var)",
    )
    parser.add_argument(
        "--paused",
        action="store_true",
        help="Flag indicating repository dispatch is paused",
    )

    args = parser.parse_args(argv)

    if args.dry_run_path:
        return run_dispatch_dry_run(args.dry_run_path, concurrency_limit=args.concurrency)

    target_path = args.path or "."
    pipeline_token = args.token
    jules_api_key = args.jules_key
    repo = args.repo

    paused_var = os.environ.get("TICKET_ENGINE_PAUSED")
    is_paused = args.paused or (
        bool(paused_var) and paused_var.strip().lower() not in ("0", "false", "no", "")
    )

    if pipeline_token and jules_api_key and repo:
        return run_live_dispatch_cli(
            path_str=target_path,
            repo=repo,
            pipeline_token=pipeline_token,
            jules_api_key=jules_api_key,
            paused=is_paused,
        )

    return run_dispatch_dry_run(target_path, concurrency_limit=args.concurrency)


if __name__ == "__main__":
    sys.exit(main())
