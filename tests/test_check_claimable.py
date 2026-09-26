"""Tests for scripts/check_claimable.py -- live-remote claimability check (ticket 38).

WHY THIS EXISTS
---------------
Ticket 38 AC: the script drives its git commands (fetch, ls-tree, the git show
that reads the matched ticket file, and ls-remote) entirely through an injected
run_fn, parses the real ticket text with the real TicketParser, and hands the
result to the pure `claim_precheck` decision. These tests prove the argument
lists sent to run_fn, in order, and the decision line and exit code the script
prints and returns -- all from a fake `run_fn`. Real git and real network are
never touched.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import check_claimable

TICKET_38_BODY = """# 38: Check claimable before claiming

**Status:** ready-for-agent

**Runner:** any
"""

TICKET_07_BODY = """# 07: Some earlier ticket

**Status:** ready-for-agent

**Runner:** any
"""


def _cp(stdout: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


class FakeRunFn:
    """Records every argument list it is called with, in order, and returns
    scripted `subprocess.CompletedProcess`-like results in sequence."""

    def __init__(self, responses: list[subprocess.CompletedProcess]) -> None:
        self._responses = list(responses)
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(args)
        idx = len(self.calls) - 1
        if idx < len(self._responses):
            return self._responses[idx]
        return _cp("")


def test_script_drives_commands_in_order_and_parses_real_file(capsys):
    fake = FakeRunFn(
        [
            _cp(),  # git fetch
            _cp(".scratch/box-primary-worker/issues/38-check-claimable-before-claiming.md\n"),
            _cp(TICKET_38_BODY),  # git show
            _cp("abc123\trefs/heads/claim/box-primary-worker/38\n"),  # git ls-remote
        ]
    )

    rc = check_claimable.main(fake, "box-primary-worker", 38)

    assert rc == 1
    assert capsys.readouterr().out.strip() == "already-claimed: box-primary-worker 38"
    assert fake.calls == [
        ["git", "fetch", "origin", "master"],
        [
            "git",
            "ls-tree",
            "-r",
            "--name-only",
            "origin/master",
            ".scratch/box-primary-worker/issues/",
        ],
        [
            "git",
            "show",
            "origin/master:.scratch/box-primary-worker/issues/38-check-claimable-before-claiming.md",
        ],
        [
            "git",
            "ls-remote",
            "--heads",
            "origin",
            "claim/box-primary-worker/38",
            "ticket/box-primary-worker-38-*",
        ],
    ]


def test_double_digit_number_renders_07_in_paths_and_refs():
    fake = FakeRunFn(
        [
            _cp(),
            _cp(".scratch/box-primary-worker/issues/07-some-earlier-ticket.md\n"),
            _cp(TICKET_07_BODY),
            _cp(""),  # no claim refs: claimable
        ]
    )

    rc = check_claimable.main(fake, "box-primary-worker", 7)

    assert rc == 0
    assert fake.calls[1] == [
        "git",
        "ls-tree",
        "-r",
        "--name-only",
        "origin/master",
        ".scratch/box-primary-worker/issues/",
    ]
    assert fake.calls[2] == [
        "git",
        "show",
        "origin/master:.scratch/box-primary-worker/issues/07-some-earlier-ticket.md",
    ]
    assert fake.calls[3] == [
        "git",
        "ls-remote",
        "--heads",
        "origin",
        "claim/box-primary-worker/07",
        "ticket/box-primary-worker-07-*",
    ]


def test_no_matching_ticket_file_is_unreadable_not_a_crash(capsys):
    fake = FakeRunFn(
        [
            _cp(),
            _cp(""),  # ls-tree: no matching path at all
        ]
    )

    rc = check_claimable.main(fake, "box-primary-worker", 38)

    assert rc == 1
    assert capsys.readouterr().out.strip() == "unreadable: box-primary-worker 38"
    # git show and git ls-remote are never called once ls-tree found no match.
    assert len(fake.calls) == 2


def test_two_matching_ticket_files_is_unreadable_not_a_crash(capsys):
    fake = FakeRunFn(
        [
            _cp(),
            _cp(
                ".scratch/box-primary-worker/issues/38-a.md\n"
                ".scratch/box-primary-worker/issues/38-b.md\n"
            ),
        ]
    )

    rc = check_claimable.main(fake, "box-primary-worker", 38)

    assert rc == 1
    assert capsys.readouterr().out.strip() == "unreadable: box-primary-worker 38"
    assert len(fake.calls) == 2


def test_claimable_exits_zero(capsys):
    fake = FakeRunFn(
        [
            _cp(),
            _cp(".scratch/box-primary-worker/issues/38-check-claimable-before-claiming.md\n"),
            _cp(TICKET_38_BODY),
            _cp(""),  # empty ls-remote output: an empty list, not an error
        ]
    )

    rc = check_claimable.main(fake, "box-primary-worker", 38)

    assert rc == 0
    assert capsys.readouterr().out.strip() == "claimable: box-primary-worker 38"
