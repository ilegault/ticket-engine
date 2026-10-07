"""Tests for integrity gate Check 9: every named test exists and failed on base code.

WHY THIS EXISTS
---------------
ADR 0013 and ticket 74 establish Check 9: every test named in a ticket's acceptance
criteria (a backticked `test_...`) must exist on the PR's head and must have failed
on the base code (as verified by Check 7), unless the criterion explicitly says it
rewrites an existing test. This prevents tickets from being ticked off without real,
failing-first test proof.
"""
from __future__ import annotations

import textwrap

from ticket_engine.integrity import (
    BaseTestResults,
    IntegrityCore,
    Verdict,
    named_test_problems,
)
from ticket_engine.parser import TicketParser

# ---------------------------------------------------------------------------
# Pure named_test_problems tests
# ---------------------------------------------------------------------------

def test_named_test_problems_missing_test():
    raw_ticket = textwrap.dedent("""
        # 10: Feature
        **Status:** done
        ## Acceptance criteria
        - [x] Feature works `test_alpha` and `test_beta`
    """)
    problems = named_test_problems(
        raw_ticket=raw_ticket,
        head_test_names={"test_alpha"},
        failed_on_base_names=None,
    )
    assert len(problems) == 1
    assert 'Check 9 fail: criterion "Feature works `test_alpha` and `test_beta`" names `test_beta`, which is not in the PR\'s tests' in problems[0]


def test_named_test_problems_did_not_fail_on_base():
    raw_ticket = textwrap.dedent("""
        # 10: Feature
        **Status:** done
        ## Acceptance criteria
        - [x] Feature works `test_alpha`
    """)
    problems = named_test_problems(
        raw_ticket=raw_ticket,
        head_test_names={"test_alpha"},
        failed_on_base_names=set(),
    )
    assert len(problems) == 1
    assert 'Check 9 fail: `test_alpha` (criterion "Feature works `test_alpha`") did not fail on the base code, so it does not prove the criterion' in problems[0]


def test_named_test_problems_rewrite_exempt():
    raw_ticket = textwrap.dedent("""
        # 10: Feature
        **Status:** done
        ## Acceptance criteria
        - [x] Rewrite `test_old` in place to assert new behavior
    """)
    problems = named_test_problems(
        raw_ticket=raw_ticket,
        head_test_names={"test_old"},
        failed_on_base_names=set(),
    )
    assert problems == []


def test_named_test_problems_rewrites_and_rewritten_exempt():
    raw_ticket = textwrap.dedent("""
        # 10: Feature
        **Status:** done
        ## Acceptance criteria
        - [x] rewrites `test_one` for edge cases
        - [x] rewritten `test_two` in place
    """)
    problems = named_test_problems(
        raw_ticket=raw_ticket,
        head_test_names={"test_one", "test_two"},
        failed_on_base_names=set(),
    )
    assert problems == []


def test_named_test_problems_multiline_wrapped_criterion():
    raw_ticket = textwrap.dedent("""
        # 10: Feature
        **Status:** done
        ## Acceptance criteria
        - [x] First line of criterion that wraps
          onto a second line with `test_wrapped`
    """)
    problems = named_test_problems(
        raw_ticket=raw_ticket,
        head_test_names=set(),
        failed_on_base_names=None,
    )
    assert len(problems) == 1
    assert "names `test_wrapped`" in problems[0]
    assert "onto a second line" in problems[0]


def test_named_test_problems_none_failed_on_base_only_checks_presence():
    raw_ticket = textwrap.dedent("""
        # 10: Feature
        **Status:** done
        ## Acceptance criteria
        - [x] Test `test_alpha` exists
    """)
    problems = named_test_problems(
        raw_ticket=raw_ticket,
        head_test_names={"test_alpha"},
        failed_on_base_names=None,
    )
    assert problems == []


# ---------------------------------------------------------------------------
# Acceptance criteria tests using IntegrityCore.evaluate
# ---------------------------------------------------------------------------

def test_check9_fails_when_a_named_test_is_missing():
    """A missing named test fails.
    A done ticket names test_alpha and test_beta, the head has only test_alpha;
    asserts verdict fail and a reason naming test_beta.
    """
    base_tree = {
        "tests/test_sample.py": "def test_existing():\n    pass\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,2 +1,4 @@
         def test_existing():
             pass
        +def test_alpha():
        +    pass
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Feature
        **Status:** done
        ## Acceptance criteria
        - [x] Criteria names `test_alpha` and `test_beta`
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-feature.md")
    core = IntegrityCore()

    base_results = BaseTestResults(
        new_tests=["tests/test_sample.py::test_alpha"],
        failed_tests=["tests/test_sample.py::test_alpha"],
    )

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        base_test_results=base_results,
    )

    assert verdict.verdict == Verdict.FAIL
    assert verdict.is_fail()
    assert any("test_beta" in r and "Check 9 fail" in r for r in verdict.reasons)


def test_check9_fails_when_a_named_test_passed_on_base():
    """A named test that passed on the old code fails.
    test_alpha is new but not in failed_tests; asserts fail and a reason
    containing 'did not fail on the base code'.
    """
    base_tree = {
        "tests/test_sample.py": "def test_existing():\n    pass\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,2 +1,4 @@
         def test_existing():
             pass
        +def test_alpha():
        +    pass
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Feature
        **Status:** done
        ## Acceptance criteria
        - [x] Feature works `test_alpha`
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-feature.md")
    core = IntegrityCore()

    base_results = BaseTestResults(
        new_tests=["tests/test_sample.py::test_alpha"],
        passed_tests=["tests/test_sample.py::test_alpha"],
        failed_tests=[],
    )

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        base_test_results=base_results,
    )

    assert verdict.verdict == Verdict.FAIL
    assert verdict.is_fail()
    assert any("did not fail on the base code" in r for r in verdict.reasons)


def test_check9_rewrite_criterion_needs_only_an_existing_test():
    """A rewrite criterion needs only an existing test.
    A criterion 'Rewrite `test_old` in place …' with test_old present on base and
    head and not failing on base; asserts no check 9 reason.
    """
    base_tree = {
        "tests/test_sample.py": "def test_old():\n    assert True\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,2 +1,2 @@
        -def test_old():
        -    assert True
        +def test_old():
        +    assert 1 == 1
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Feature
        **Status:** done
        ## Acceptance criteria
        - [x] Rewrite `test_old` in place to assert new behavior
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-feature.md")
    core = IntegrityCore()

    base_results = BaseTestResults(
        new_tests=[],
        passed_tests=[],
        failed_tests=[],
    )

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        base_test_results=base_results,
    )

    assert not any("Check 9" in r for r in verdict.reasons)
    assert verdict.verdict == Verdict.PASS


def test_check9_silent_when_the_ticket_names_no_tests():
    """No named tests, no check 9.
    Asserts the verdict and reasons equal what they were without check 9
    (pass, summary checks 1-9).
    """
    base_tree = {
        "tests/test_sample.py": "def test_existing():\n    pass\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,2 +1,4 @@
         def test_existing():
             pass
        +def test_new():
        +    pass
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Feature
        **Status:** done
        ## Acceptance criteria
        - [x] Feature works without naming tests
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-feature.md")
    core = IntegrityCore()

    base_results = BaseTestResults(
        new_tests=["tests/test_sample.py::test_new"],
        passed_tests=[],
        failed_tests=["tests/test_sample.py::test_new"],
        runtime_seconds=0.1,
    )

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        base_test_results=base_results,
    )

    assert verdict.verdict == Verdict.PASS
    assert verdict.is_pass()
    assert "All integrity checks passed (checks 1-9)" in verdict.reasons
    assert not any("Check 9" in r for r in verdict.reasons)


def test_check9_passes_when_every_named_test_is_new_and_failed_on_base():
    """Every named test that did its job passes.
    Two named tests, both new and both in failed_tests; asserts verdict pass.
    """
    base_tree = {
        "tests/test_sample.py": "def test_existing():\n    pass\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,2 +1,6 @@
         def test_existing():
             pass
        +def test_alpha():
        +    pass
        +def test_beta():
        +    pass
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Feature
        **Status:** done
        ## Acceptance criteria
        - [x] First criterion `test_alpha`
        - [x] Second criterion `test_beta`
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-feature.md")
    core = IntegrityCore()

    base_results = BaseTestResults(
        new_tests=["tests/test_sample.py::test_alpha", "tests/test_sample.py::test_beta"],
        passed_tests=[],
        failed_tests=["tests/test_sample.py::test_alpha", "tests/test_sample.py::test_beta"],
        runtime_seconds=0.2,
    )

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        base_test_results=base_results,
    )

    assert verdict.verdict == Verdict.PASS
    assert verdict.is_pass()
    assert "All integrity checks passed (checks 1-9)" in verdict.reasons
    assert not any("Check 9" in r for r in verdict.reasons)
