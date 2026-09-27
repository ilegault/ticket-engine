"""Tests for ticket 43: CONTEXT.md matches the built Sonnet fallback (tickets 39-41).

WHY THIS EXISTS
---------------
Ticket 43 brings the repo's binding glossary in line with what tickets 39-41
actually built: an optional Claude Sonnet fallback the local worker tries on
an agy quota error before pausing. These are plain text assertions on the doc
itself, since the doc is the contract other sessions read (AGENTS.md §8), the
same shape `tests/test_phase2_docs.py` uses for ticket 34.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONTEXT_PATH = ROOT / "CONTEXT.md"
HASHES_PATH = (
    pathlib.Path(__file__).parent
    / "fixtures"
    / "sonnet_fallback_docs"
    / "context_md_paragraph_hashes.json"
)

# Paragraphs (split on a blank line) that this ticket is authorised to change.
_EDITED_PARAGRAPH_PREFIXES = (
    "**Quota reserve**",
    "**Worker** — anything that implements a ticket",
)


def _paragraphs() -> list[str]:
    return CONTEXT_PATH.read_text(encoding="utf-8").split("\n\n")


def test_context_quota_reserve_extended_not_replaced():
    """The existing local-worker sentence is kept, and a new one names the fallback."""
    text = CONTEXT_PATH.read_text(encoding="utf-8")
    assert (
        "Local worker: no pre-flight reserve (agy exposes no quota reading); "
        "it pauses on a quota error and keeps its claim."
    ) in text
    quota_entry = next(p for p in _paragraphs() if p.startswith("**Quota reserve**"))
    assert "Sonnet" in quota_entry
    assert "quota" in quota_entry.split("Sonnet fallback")[-1]


def test_context_local_worker_names_sonnet_fallback():
    """The Local worker bullet gains a clause naming the optional Sonnet fallback."""
    worker_entry = next(
        p for p in _paragraphs() if p.startswith("**Worker** — anything that implements a ticket")
    )
    assert "Sonnet" in worker_entry
    assert "one ticket, one PR" in worker_entry


def test_context_no_other_entries_changed():
    """Every glossary paragraph other than the two edited ones is unchanged."""
    paragraphs = _paragraphs()
    snapshot = json.loads(HASHES_PATH.read_text(encoding="utf-8"))

    assert len(paragraphs) == snapshot["total_paragraphs"]

    for index, paragraph in enumerate(paragraphs):
        if any(paragraph.startswith(prefix) for prefix in _EDITED_PARAGRAPH_PREFIXES):
            continue
        expected_hash = snapshot["hashes"][str(index)]
        actual_hash = hashlib.sha256(paragraph.encode("utf-8")).hexdigest()
        assert actual_hash == expected_hash, (
            f"CONTEXT.md paragraph {index} changed outside this ticket's scope: "
            f"{paragraph[:80]!r}"
        )
