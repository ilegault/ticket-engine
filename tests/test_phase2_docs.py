"""Tests for ticket 34: CONTEXT.md, the issue-tracker doc and ADR 0007 match the built box.

WHY THIS EXISTS
---------------
Ticket 34 brings the repo's binding docs in line with what tickets 19-31
actually built (no pre-flight quota reserve for the local worker, the local
worker/box taking any ticket, the `Claimed-by:` line, and the box's real
GitHub token scopes). These are plain text assertions on the docs themselves,
since the docs are the contract other sessions read (AGENTS.md Sec.8).
"""
from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTEXT_PATH = ROOT / "CONTEXT.md"
ISSUE_TRACKER_PATH = ROOT / "docs" / "agents" / "issue-tracker.md"
ADR_0007_PATH = ROOT / "docs" / "adr" / "0007-the-box-runs-agy-unrestricted-inside-a-fenced-account.md"


def test_context_quota_reserve_no_pre_flight_for_local_worker():
    text = CONTEXT_PATH.read_text(encoding="utf-8")
    assert "20%" not in text
    assert (
        "Local worker: no pre-flight reserve (agy exposes no quota reading); "
        "it pauses on a quota error and keeps its claim."
    ) in text
    assert (
        "Jules: never start a\nsession when fewer than 10 of the rolling-24-hour allowance remain."
        in text
    )


def test_context_local_worker_and_checkpoint():
    text = CONTEXT_PATH.read_text(encoding="utf-8")
    assert "takes any ticket" in text
    assert "the local worker pushes" in text


def test_issue_tracker_documents_claimed_by():
    text = ISSUE_TRACKER_PATH.read_text(encoding="utf-8")
    assert (
        "- A `Claimed-by:` line (`box` or `jules`) is written under `Status:` "
        "when a worker claims the ticket. Workers keep it; status words are unchanged."
    ) in text


def test_adr_0007_amendment_adds_variables_read():
    text = ADR_0007_PATH.read_text(encoding="utf-8")
    rule_3 = text.split("3. **The box's GitHub token")[1].split("4.")[0]
    assert "Variables" in rule_3
    assert "## Amendment" in text
