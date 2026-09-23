"""Fixture PR tests for integrity gate checks.

WHY THIS EXISTS
---------------
Phase 1 Spec §Testing Decisions and Ticket 03 acceptance criteria require:
'One fixture PR per check proves it fires; one clean fixture proves pass.
Each fixture test was observed failing before its check existed.'
"""
from __future__ import annotations

import pathlib

from ticket_engine.integrity import IntegrityCore, Verdict

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures" / "integrity"


def _load_fixture(fixture_name: str) -> tuple[dict[str, str], str, str]:
    fix_dir = _FIXTURES_DIR / fixture_name
    base_dir = fix_dir / "base"
    base_tree: dict[str, str] = {}
    if base_dir.is_dir():
        for p in base_dir.rglob("*.py"):
            rel_path = p.relative_to(base_dir).as_posix()
            base_tree[rel_path] = p.read_text(encoding="utf-8")

    diff_path = fix_dir / "pr.diff"
    pr_diff = diff_path.read_text(encoding="utf-8") if diff_path.is_file() else ""

    ticket_path = fix_dir / "ticket.md"
    ticket_content = ticket_path.read_text(encoding="utf-8") if ticket_path.is_file() else ""

    return base_tree, pr_diff, ticket_content


def test_fixture_clean_pr_passes():
    base_tree, pr_diff, ticket_content = _load_fixture("clean_pr")
    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
        test_results={"passed": True},
    )
    assert verdict.verdict == Verdict.PASS


def test_fixture_check1_newly_skipped_fails():
    base_tree, pr_diff, ticket_content = _load_fixture("check1_skipped")
    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
    )
    assert verdict.verdict == Verdict.FAIL
    assert any("test_add" in r for r in verdict.reasons)


def test_fixture_check2_deleted_test_fails():
    base_tree, pr_diff, ticket_content = _load_fixture("check2_deleted_test")
    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
    )
    assert verdict.verdict == Verdict.FAIL
    assert any("test_sub" in r for r in verdict.reasons)


def test_fixture_check2_fewer_assertions_fails():
    base_tree, pr_diff, ticket_content = _load_fixture("check2_fewer_assertions")
    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
    )
    assert verdict.verdict == Verdict.FAIL
    assert any("fewer assertions" in r.lower() or "lost" in r.lower() for r in verdict.reasons)


def test_fixture_check6_not_done_or_unticked_fails():
    base_tree, pr_diff, ticket_content = _load_fixture("check6_unticked")
    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
    )
    assert verdict.verdict == Verdict.FAIL
    assert any("unticked" in r.lower() or "not ticked" in r.lower() for r in verdict.reasons)
