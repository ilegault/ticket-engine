"""Ticket 34: CONTEXT.md, the issue-tracker doc, and ADR 0007 match the built box.

WHY THIS EXISTS
---------------
Tickets 19-31 built the box as it now stands: a local worker that takes any
ticket with no pre-flight quota reserve, a `Claimed-by:` line written on every
claim, and a box-worker loop that reads the target repo's `TICKET_ENGINE_PAUSED`
variable. This repo's binding docs (`CONTEXT.md`, `docs/agents/issue-tracker.md`,
ADR 0007) described the pre-ticket-31 design. This test reads the three files as
plain text and asserts they now say what the code does; nothing is faked.
"""
from __future__ import annotations

import pathlib
import re

_ROOT = pathlib.Path(__file__).parent.parent
_CONTEXT = (_ROOT / "CONTEXT.md").read_text(encoding="utf-8")
_ISSUE_TRACKER = (_ROOT / "docs" / "agents" / "issue-tracker.md").read_text(encoding="utf-8")
_ADR_0007 = (
    _ROOT / "docs" / "adr" / "0007-the-box-runs-agy-unrestricted-inside-a-fenced-account.md"
).read_text(encoding="utf-8")


def _entry(text: str, term: str) -> str:
    """The glossary entry for `**term**` up to the next blank line, unwrapped
    to a single line so a hard-wrapped source line never breaks a substring
    check across two of the test's own strings."""
    match = re.search(rf"\*\*{re.escape(term)}\*\*.*?(?=\n\n)", text, re.DOTALL)
    assert match, f"No glossary entry found for {term!r}"
    return re.sub(r"\s+", " ", match.group(0))


def test_quota_reserve_has_no_pre_flight_reserve_for_local_worker():
    entry = _entry(_CONTEXT, "Quota reserve")
    assert "20%" not in entry
    assert (
        "Local worker: no pre-flight reserve (agy exposes no quota reading); "
        "it pauses on a quota error and keeps its claim." in entry
    )
    assert "Jules: never start a" in entry, "The Jules half of the entry must be unchanged"


def test_local_worker_takes_any_ticket_and_links_to_box():
    entry = _entry(_CONTEXT, "Local worker")
    assert "takes any ticket" in entry
    assert "*Box*" in entry


def test_checkpoint_says_agy_commits_and_local_worker_pushes():
    entry = _entry(_CONTEXT, "Checkpoint")
    assert "the local worker pushes" in entry


def test_issue_tracker_documents_claimed_by_line():
    expected = (
        "A `Claimed-by:` line (`box` or `jules`) is written under `Status:` "
        "when a worker claims the ticket. Workers keep it; status words are unchanged."
    )
    assert expected in _ISSUE_TRACKER


def test_adr_0007_rule_3_lists_variables_read():
    match = re.search(r"^3\..*?(?=^\d\.|\Z)", _ADR_0007, re.DOTALL | re.MULTILINE)
    assert match, "ADR 0007 rule 3 not found"
    assert "Variables" in match.group(0)


def test_adr_0007_has_dated_amendment_section():
    assert re.search(r"^## Amendment — \d{4}-\d{2}-\d{2}$", _ADR_0007, re.MULTILINE)
