"""Ticket lint: find tickets an agent cannot land as written.

WHY THIS EXISTS
---------------
ADR 0005. Slackbot ticket 49 told its worker to delete two test functions.
Integrity check 2 fails every deleted test function unless the ticket authorises it,
so no worker could ever land that ticket. The worker spent a session finding that out,
and the ticket would have escalated. A ticket like that is a planning bug, and it is
cheapest to catch before any agent claims it.

`lint_ticket` is pure (plain `Ticket` in, reasons out). The dispatch core does not
start a ticket with findings, and the dry run and the run report list them, so the
planner sees them before handing off and the developer sees them on every run.

Rules are deliberately narrow, so a finding means a real problem and never idles a
repo on noise:

1. A paragraph or list item that tells the worker to delete or remove a named test
   function (a backticked `test_...` identifier), not negated ("do not delete"),
   when the ticket's `Deletes tests:` line does not list that function.
2. A `Deletes tests:` entry that is not `<test file>.py::<test name>`.
3. No acceptance-criteria checkboxes at all: check 6 would have nothing to verify.

Only `ready-for-agent` tickets read from a file are linted. Nothing else can be
claimed, and a ticket with no text has nothing to judge.
"""
from __future__ import annotations

import re
from collections.abc import Sequence

from ticket_engine.parser import Ticket

_DELETE_WORD_RE = re.compile(r"\b(delete|deletes|remove|removes)\b", re.IGNORECASE)
_NEGATED_RE = re.compile(
    r"\b(do not|don't|dont|never|not|must not|mustn't)\s+(delete|remove)\b", re.IGNORECASE
)
_TEST_IDENT_RE = re.compile(r"`(test_\w+)`")
_DELETES_ENTRY_RE = re.compile(r"^[\w./-]+\.py::\w+(?:::\w+)?$")
_CHECKBOX_RE = re.compile(r"^\s*[-*]\s*\[[ xX]\]", re.MULTILINE)
_BLOCK_START_RE = re.compile(r"^\s*(?:[-*]\s|\d+\.\s|#)")


def _blocks(text: str) -> list[str]:
    """Split markdown into paragraphs and list items (a list item keeps its
    indented continuation lines)."""
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if not line.strip():
            if current:
                blocks.append(" ".join(current))
                current = []
            continue
        if _BLOCK_START_RE.match(line) and current:
            blocks.append(" ".join(current))
            current = []
        current.append(line.strip())
    if current:
        blocks.append(" ".join(current))
    return blocks


def lint_ticket(ticket: Ticket) -> list[str]:
    """Reasons an agent cannot land this ticket as written. [] means none found."""
    text = ticket.raw_text or ""
    # No text means the ticket was not read from a file (an in-memory record); there
    # is nothing to judge. A real file with no text has no Status line either.
    if ticket.status != "ready-for-agent" or not text.strip():
        return []
    findings: list[str] = []

    authorised_names = {entry.split("::")[-1] for entry in ticket.deletes_tests}
    seen: set[str] = set()
    for block in _blocks(text):
        if block.lower().lstrip("*").startswith("deletes tests:"):
            continue
        if not _DELETE_WORD_RE.search(block) or _NEGATED_RE.search(block):
            continue
        for name in _TEST_IDENT_RE.findall(block):
            if name in authorised_names or name in seen:
                continue
            seen.add(name)
            findings.append(
                f"tells the worker to delete `{name}`, but no `Deletes tests:` line lists "
                "it, so integrity check 2 would fail the PR"
            )

    for entry in ticket.deletes_tests:
        if not _DELETES_ENTRY_RE.match(entry):
            findings.append(
                f"`Deletes tests:` entry `{entry}` is not `<test file>.py::<test name>`"
            )

    if not _CHECKBOX_RE.search(text):
        findings.append(
            "has no acceptance-criteria checkboxes, so integrity check 6 has nothing to verify"
        )
    return findings


def lint_tickets(tickets: Sequence[Ticket]) -> dict[int, list[str]]:
    """Findings by ticket number, only for tickets that have any."""
    out: dict[int, list[str]] = {}
    for t in tickets:
        found = lint_ticket(t)
        if found:
            out[t.number] = found
    return out
