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
        for p in base_dir.rglob("*"):
            if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc"):
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


def test_fixture_check3_ratchet_increased_fails():
    base_tree, pr_diff, ticket_content = _load_fixture("check3_ratchet")
    from ticket_engine.integrity import IntegrityConfig
    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
        config=IntegrityConfig(ratchet_files=[".ratchet"]),
    )
    assert verdict.verdict == Verdict.FAIL
    assert any("Check 3 fail" in r and ".ratchet" in r for r in verdict.reasons)


def test_fixture_check4_protected_path_holds():
    base_tree, pr_diff, ticket_content = _load_fixture("check4_hold")
    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
    )
    assert verdict.verdict == Verdict.HOLD
    assert any("Check 4 hold" in r and ".github/workflows/ci.yml" in r for r in verdict.reasons)


def test_fixture_check5_escape_hatch_holds():
    base_tree, pr_diff, ticket_content = _load_fixture("check5_escape_hatch")
    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
        commit_messages=["docs: update readme [no-test-needed: docs update]"],
    )
    assert verdict.verdict == Verdict.HOLD
    assert any("Check 5 hold" in r for r in verdict.reasons)


def test_fixture_automerge_no_holds():
    base_tree, pr_diff, ticket_content = _load_fixture("automerge_no")
    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
    )
    assert verdict.verdict == Verdict.HOLD
    assert any("auto-merge" in r.lower() for r in verdict.reasons)


def test_fixture_denylist_match_fails():
    base_tree, pr_diff, ticket_content = _load_fixture("denylist_match")
    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
        denylist=["Jane Doe"],
    )
    assert verdict.verdict == Verdict.FAIL
    assert any("src/app.py" in r and "line 2" in r for r in verdict.reasons)
    # Ensure secret is not in reasons
    assert "Jane Doe" not in " ".join(verdict.reasons)


def test_fixture_fail_and_hold_fails():
    base_tree, pr_diff, ticket_content = _load_fixture("fail_and_hold")
    core = IntegrityCore()
    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket_content,
    )
    assert verdict.verdict == Verdict.FAIL
    assert any("Check 2 fail" in r for r in verdict.reasons)
    assert any("Check 4 hold" in r for r in verdict.reasons)

