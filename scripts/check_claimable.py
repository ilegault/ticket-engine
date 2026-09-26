"""Check whether a ticket is still claimable against the live remote, before claiming.

WHY THIS EXISTS
---------------
Ticket 38: a session working a ticket outside the dispatcher's own loop (an ad
hoc human/agent session, not a Jules or box run `DispatchCore.evaluate`
started) can begin orienting on a ticket at the exact moment the box or a
Jules session legitimately claims, implements and merges it. Nothing was lost
the first time this happened -- the session's own orientation re-check caught
the staleness before it wrote a line of code -- but the ticket skill never
*made* that check mandatory or automatic. This script gives every worker,
whatever started it, one small thing to run immediately before creating a
claim branch: fetch the default branch, read the ticket file as it exists
there (never the worker's own possibly-stale checkout), list any remote refs
that already claim the ticket, and hand all of that to the pure
`dispatch.claim_precheck` decision -- reused rather than a second
claimed-check (AGENTS.md Sec.3 invariant 3).

All I/O goes through the injected `run_fn`; this module never calls
`subprocess.run` directly, so tests drive it entirely with a fake (no real
git, no real network, no token needed).
"""
from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Callable

from ticket_engine.dispatch import claim_precheck
from ticket_engine.parser import TicketParser

RunFn = Callable[[list[str]], subprocess.CompletedProcess]


def main(
    run_fn: RunFn,
    effort: str,
    number: int,
    default_branch: str = "master",
) -> int:
    """Print ``<decision>: <effort> <NN>`` and return 0 for "claimable", else 1."""
    num = f"{number:02d}"

    # 1. Fetch the default branch: the ticket file and claim refs are read from
    # the live remote, never from this worktree's own possibly-stale checkout.
    run_fn(["git", "fetch", "origin", default_branch])

    # 2. Find the one ticket file matching this number on the default branch,
    # then read its real content there.
    issues_dir = f".scratch/{effort}/issues/"
    ls_tree = run_fn(
        ["git", "ls-tree", "-r", "--name-only", f"origin/{default_branch}", issues_dir]
    )
    file_re = re.compile(rf"^{re.escape(issues_dir)}{re.escape(num)}-[^/]+\.md$")
    matches = [
        line.strip()
        for line in (ls_tree.stdout or "").splitlines()
        if file_re.fullmatch(line.strip())
    ]

    if len(matches) != 1:
        decision = "unreadable"
        print(f"{decision}: {effort} {num}")
        return 1

    path = matches[0]
    show = run_fn(["git", "show", f"origin/{default_branch}:{path}"])
    ticket = TicketParser().parse_text(show.stdout or "", filename=path)

    # 3. Any remote ref that would already claim this ticket, whatever fetched
    # this worktree last. Empty output is an empty claim-ref list, not an error.
    ls_remote = run_fn(
        [
            "git",
            "ls-remote",
            "--heads",
            "origin",
            f"claim/{effort}/{num}",
            f"ticket/{effort}-{num}-*",
        ]
    )
    claim_refs = [line for line in (ls_remote.stdout or "").splitlines() if line.strip()]

    decision = claim_precheck(ticket, claim_refs)
    print(f"{decision}: {effort} {num}")
    return 0 if decision == "claimable" else 1


def _real_run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, check=False)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: check_claimable.py <effort> <NN> [default_branch]", file=sys.stderr)
        sys.exit(2)
    _effort = sys.argv[1]
    _number = int(sys.argv[2])
    _default_branch = sys.argv[3] if len(sys.argv) > 3 else "master"
    sys.exit(main(_real_run, _effort, _number, _default_branch))
