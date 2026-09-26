"""Ticket 38 AC: the ticket skill mandates the live-claimability check.

WHY THIS EXISTS
---------------
Ticket 38 requires the "Claim the ticket" step under Sec.1 of the ticket skill to
tell every worker to run `scripts/check_claimable.py` immediately before
creating a claim branch, and to stop rather than claim on any decision other
than "claimable". This test reads the skill text itself so a future edit
cannot silently drop that instruction.
"""
from __future__ import annotations

import pathlib

_SKILL_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "src"
    / "ticket_engine"
    / "resources"
    / "ticket_skill.md"
)


def _claim_section(text: str) -> str:
    start = text.index("### Claim the ticket")
    end = text.index("\n## ", start)
    return text[start:end]


def test_claim_the_ticket_section_requires_check_claimable_script():
    text = _SKILL_PATH.read_text(encoding="utf-8")
    section = _claim_section(text)

    assert (
        "Run `python scripts/check_claimable.py <effort> <NN>` immediately before "
        "creating the claim branch."
    ) in section
    assert "stop, do not claim it" in section
