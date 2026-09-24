"""Tests for pure integrity gate core.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Integrity core), User Stories 27-37,
and ADR 0001 require that unattended merges only proceed behind an integrity
gate. Checks 1, 2, and 6 ensure that workers cannot achieve green builds by
muting tests (check 1), deleting tests or weakening assertions (check 2),
or leaving ticket criteria unverified (check 6).
All decisions must be evaluated by a pure core without I/O.
"""
from __future__ import annotations

import textwrap

from ticket_engine.integrity import (
    BaseTestResults,
    IntegrityConfig,
    IntegrityCore,
    IntegrityVerdict,
    Verdict,
    find_new_test_functions,
)
from ticket_engine.parser import TicketParser


def test_clean_pr_passes():
    base_tree = {
        "tests/test_sample.py": "def test_one():\n    assert 1 + 1 == 2\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -2,1 +2,3 @@
             assert 1 + 1 == 2
        +def test_two():
        +    assert 2 + 2 == 4
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Clean feature
        **Status:** done
        **Blocked by:** None
        **Runner:** any
        **Auto-merge:** yes

        ## Acceptance criteria
        - [x] Feature works
        - [x] Tests pass
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-clean-feature.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        config=IntegrityConfig(),
        test_results={"passed": True},
    )

    assert isinstance(verdict, IntegrityVerdict)
    assert verdict.verdict == Verdict.PASS
    assert verdict.is_pass()
    assert not verdict.is_fail()
    assert not verdict.is_hold()


def test_check_1_newly_skipped_pytest_mark_skip_fails():
    base_tree = {
        "tests/test_sample.py": textwrap.dedent("""
            def test_existing():
                assert True
        """),
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,2 +1,4 @@
        +import pytest
        +@pytest.mark.skip(reason="broken")
         def test_existing():
             assert True
    """)
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] Done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.FAIL
    assert verdict.is_fail()
    assert any("test_existing" in r for r in verdict.reasons)
    assert any("tests/test_sample.py" in r for r in verdict.reasons)


def test_check_1_newly_skipped_pytest_mark_skipif_fails():
    base_tree = {
        "tests/test_sample.py": textwrap.dedent("""
            def test_one():
                assert True
        """),
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,2 +1,4 @@
        +import pytest
        +@pytest.mark.skipif(True, reason="skip condition")
         def test_one():
             assert True
    """)
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] Done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.FAIL
    assert any("test_one" in r for r in verdict.reasons)


def test_check_1_newly_skipped_pytest_mark_xfail_fails():
    base_tree = {
        "tests/test_sample.py": textwrap.dedent("""
            def test_flaky():
                assert True
        """),
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,2 +1,4 @@
        +import pytest
        +@pytest.mark.xfail
         def test_flaky():
             assert True
    """)
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] Done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.FAIL
    assert any("test_flaky" in r for r in verdict.reasons)


def test_check_1_newly_skipped_body_call_pytest_skip_fails():
    base_tree = {
        "tests/test_sample.py": textwrap.dedent("""
            def test_call():
                assert True
        """),
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,2 +1,4 @@
         def test_call():
        +    import pytest
        +    pytest.skip("cannot run")
             assert True
    """)
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] Done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.FAIL
    assert any("test_call" in r for r in verdict.reasons)


def test_check_1_pre_existing_skip_on_base_does_not_fail():
    base_tree = {
        "tests/test_sample.py": textwrap.dedent("""
            import pytest

            @pytest.mark.skip(reason="already skipped on base")
            def test_skipped_already():
                assert True

            def test_normal():
                assert True
        """),
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -7,2 +7,3 @@
         def test_normal():
             assert True
        +    assert 1 == 1
    """)
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] Done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.PASS


def test_check_2_deleted_test_function_fails():
    base_tree = {
        "tests/test_sample.py": textwrap.dedent("""
            def test_keep():
                assert True

            def test_remove():
                assert True
        """),
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -3,4 +3,1 @@
         def test_keep():
             assert True
        -
        -def test_remove():
        -    assert True
    """)
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] Done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.FAIL
    assert any("test_remove" in r for r in verdict.reasons)
    assert any("deleted" in r.lower() for r in verdict.reasons)


def test_check_2_deleted_test_file_fails():
    base_tree = {
        "tests/test_keep.py": textwrap.dedent("""
            def test_keep():
                assert True
        """),
        "tests/test_delete.py": textwrap.dedent("""
            def test_will_be_deleted():
                assert True
        """),
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_delete.py b/tests/test_delete.py
        deleted file mode 100644
        --- a/tests/test_delete.py
        +++ /dev/null
        @@ -1,3 +0,0 @@
        -def test_will_be_deleted():
        -    assert True
    """)
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] Done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.FAIL
    assert any("test_will_be_deleted" in r or "test_delete.py" in r for r in verdict.reasons)


def test_check_2_fewer_assertions_in_test_file_fails():
    base_tree = {
        "tests/test_sample.py": textwrap.dedent("""
            def test_assertions():
                assert 1 == 1
                assert 2 == 2
                assert 3 == 3
        """),
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,5 +1,4 @@
         def test_assertions():
             assert 1 == 1
        -    assert 2 == 2
             assert 3 == 3
    """)
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] Done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.FAIL
    assert any("fewer assertions" in r.lower() or "lost" in r.lower() for r in verdict.reasons)
    assert any("tests/test_sample.py" in r for r in verdict.reasons)


def test_check_6_ticket_not_done_fails():
    base_tree = {"tests/test_sample.py": "def test_a(): assert True\n"}
    pr_diff = ""
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** in-progress
        ## Acceptance criteria
        - [x] All done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.FAIL
    assert any("in-progress" in r or "not 'done'" in r.lower() or "status" in r.lower() for r in verdict.reasons)


def test_check_6_unticked_acceptance_box_fails():
    base_tree = {"tests/test_sample.py": "def test_a(): assert True\n"}
    pr_diff = ""
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] First item completed
        - [ ] Second item still pending
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.FAIL
    assert any("Second item still pending" in r or "unticked" in r.lower() for r in verdict.reasons)


def test_auto_merge_no_produces_hold():
    base_tree = {"tests/test_sample.py": "def test_a(): assert True\n"}
    pr_diff = ""
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        **Auto-merge:** no
        ## Acceptance criteria
        - [x] All done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.HOLD
    assert verdict.is_hold()
    assert not verdict.is_pass()
    assert not verdict.is_fail()
    assert any("auto-merge" in r.lower() for r in verdict.reasons)


def test_reasons_contain_no_secrets_or_code_dumps():
    base_tree = {"tests/test_sample.py": "def test_secret():\n    secret_value = 'SUPER_SECRET_KEY'\n    assert True\n"}
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,4 +1,5 @@
        +import pytest
        +@pytest.mark.skip
         def test_secret():
             secret_value = 'SUPER_SECRET_KEY'
             assert True
    """)
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] All done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.FAIL
    # Verify no secret leaked in reasons
    for reason in verdict.reasons:
        assert "SUPER_SECRET_KEY" not in reason
        assert "secret_value" not in reason


def test_check_3_ratchet_file_increased_fails():
    base_tree = {
        "tests/test_sample.py": "def test_a(): assert True\n",
        ".ratchet": "10\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/.ratchet b/.ratchet
        --- a/.ratchet
        +++ b/.ratchet
        @@ -1,1 +1,1 @@
        -10
        +12
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] All done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()
    config = IntegrityConfig(ratchet_files=[".ratchet"])

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        config=config,
    )

    assert verdict.verdict == Verdict.FAIL
    assert verdict.is_fail()
    assert any("Check 3 fail" in r and ".ratchet" in r and "12" in r for r in verdict.reasons)


def test_check_3_ratchet_file_equal_or_decreased_passes():
    base_tree = {
        "tests/test_sample.py": "def test_a(): assert True\n",
        ".ratchet": "10\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/.ratchet b/.ratchet
        --- a/.ratchet
        +++ b/.ratchet
        @@ -1,1 +1,1 @@
        -10
        +8
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] All done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()
    config = IntegrityConfig(ratchet_files=[".ratchet"])

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        config=config,
    )

    assert verdict.verdict == Verdict.PASS
    assert verdict.is_pass()


def test_check_4_touching_github_produces_hold():
    base_tree = {"tests/test_sample.py": "def test_a(): assert True\n"}
    pr_diff = textwrap.dedent("""
        diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
        --- a/.github/workflows/ci.yml
        +++ b/.github/workflows/ci.yml
        @@ -1,1 +1,2 @@
         name: CI
        +# edit
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] All done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.HOLD
    assert verdict.is_hold()
    assert any("Check 4 hold" in r and ".github/workflows/ci.yml" in r for r in verdict.reasons)


def test_check_4_touching_adr_agents_context_or_gate_produces_hold():
    for protected_file in [
        "docs/adr/0001-gate.md",
        "AGENTS.md",
        "CONTEXT.md",
        "scripts/check_tests_first.py",
    ]:
        base_tree = {
            "tests/test_sample.py": "def test_a(): assert True\n",
            protected_file: "original\n",
        }
        pr_diff = textwrap.dedent(f"""
            diff --git a/{protected_file} b/{protected_file}
            --- a/{protected_file}
            +++ b/{protected_file}
            @@ -1,1 +1,2 @@
             original
            +change
        """).strip() + "\n"
        ticket_content = textwrap.dedent("""
            # 10: Ticket
            **Status:** done
            ## Acceptance criteria
            - [x] All done
        """)
        ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
        core = IntegrityCore()

        verdict = core.evaluate(
            base_tree=base_tree,
            pr_diff=pr_diff,
            ticket=ticket,
        )

        assert verdict.verdict == Verdict.HOLD, f"Expected HOLD for touching {protected_file}"
        assert any("Check 4 hold" in r and protected_file in r for r in verdict.reasons)


def test_check_5_tests_first_escape_label_produces_hold():
    base_tree = {"tests/test_sample.py": "def test_a(): assert True\n"}
    pr_diff = ""
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] All done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        labels=["tests-exempt"],
    )

    assert verdict.verdict == Verdict.HOLD
    assert verdict.is_hold()
    assert any("Check 5 hold" in r and "tests-exempt" in r for r in verdict.reasons)


def test_check_5_commit_or_pr_tag_produces_hold():
    base_tree = {"tests/test_sample.py": "def test_a(): assert True\n"}
    pr_diff = ""
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] All done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        commit_messages=["docs: update readme [no-test-needed: doc change]"],
    )

    assert verdict.verdict == Verdict.HOLD
    assert any("Check 5 hold" in r and "no-test-needed" in r for r in verdict.reasons)




def test_when_both_fail_and_hold_reasons_exist_verdict_is_fail():
    base_tree = {
        "tests/test_sample.py": "def test_a(): assert True\n",
        ".github/workflows/ci.yml": "name: CI\n",
    }
    # Touches .github (produces hold) AND newly skips a test (produces fail)
    pr_diff = textwrap.dedent("""
        diff --git a/.github/workflows/ci.yml b/.github/workflows/ci.yml
        --- a/.github/workflows/ci.yml
        +++ b/.github/workflows/ci.yml
        @@ -1,1 +1,2 @@
         name: CI
        +# new step
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,1 +1,3 @@
        +import pytest
        +@pytest.mark.skip
         def test_a(): assert True
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Ticket
        **Status:** done
        ## Acceptance criteria
        - [x] All done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
    )

    assert verdict.verdict == Verdict.FAIL
    assert verdict.is_fail()
    assert not verdict.is_hold()

    # Reasons contain both fail and hold
    assert any("Check 1 fail" in r for r in verdict.reasons)
    assert any("Check 4 hold" in r for r in verdict.reasons)


def test_find_new_test_functions_identifies_added_tests_not_merely_edited():
    base_tree = {
        "tests/test_math.py": (
            "def test_add():\n"
            "    assert 1 + 1 == 2\n"
            "\n"
            "def test_sub():\n"
            "    assert 2 - 1 == 1\n"
        ),
    }
    # PR edits test_sub and adds test_mul
    pr_diff = (
        "diff --git a/tests/test_math.py b/tests/test_math.py\n"
        "--- a/tests/test_math.py\n"
        "+++ b/tests/test_math.py\n"
        "@@ -4,2 +4,5 @@\n"
        " def test_sub():\n"
        "-    assert 2 - 1 == 1\n"
        "+    assert 5 - 3 == 2\n"
        "+\n"
        "+def test_mul():\n"
        "+    assert 2 * 3 == 6\n"
    )

    new_tests = find_new_test_functions(base_tree, pr_diff)
    assert new_tests == ["tests/test_math.py::test_mul"]


def test_find_new_test_functions_identifies_new_files_and_class_methods():
    base_tree = {
        "tests/test_math.py": "def test_add(): assert 1 + 1 == 2\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_geom.py b/tests/test_geom.py
        new file mode 100644
        --- /dev/null
        +++ b/tests/test_geom.py
        @@ -0,0 +1,8 @@
        +def test_area():
        +    assert True
        +
        +class TestCircle:
        +    def test_radius(self):
        +        assert True
    """).strip() + "\n"

    new_tests = find_new_test_functions(base_tree, pr_diff)
    assert new_tests == [
        "tests/test_geom.py::test_area",
        "tests/test_geom.py::TestCircle::test_radius",
    ]


def test_check7_silent_when_no_new_tests_even_if_source_modified():
    base_tree = {
        "src/calc.py": "def add(a, b): return a + b\n",
        "tests/test_calc.py": "from calc import add\ndef test_add(): assert add(1, 2) == 3\n",
    }
    # PR touches source and edits existing test, but adds no new test functions
    pr_diff = textwrap.dedent("""
        diff --git a/src/calc.py b/src/calc.py
        --- a/src/calc.py
        +++ b/src/calc.py
        @@ -1,1 +1,2 @@
         def add(a, b): return a + b
        +# minor comment
        diff --git a/tests/test_calc.py b/tests/test_calc.py
        --- a/tests/test_calc.py
        +++ b/tests/test_calc.py
        @@ -2,1 +2,1 @@
        -def test_add(): assert add(1, 2) == 3
        +def test_add(): assert add(2, 3) == 5
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Refactor
        **Status:** done
        ## Acceptance criteria
        - [x] Done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        base_test_results=BaseTestResults(new_tests=[], passed_tests=[], failed_tests=[]),
    )

    assert verdict.verdict == Verdict.PASS
    assert not any("Check 7" in r for r in verdict.reasons)


def test_check7_hold_when_new_test_passes_on_base():
    base_tree = {
        "src/calc.py": "def add(a, b): return a + b\n",
        "tests/test_calc.py": "def test_add(): assert True\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_calc.py b/tests/test_calc.py
        --- a/tests/test_calc.py
        +++ b/tests/test_calc.py
        @@ -1,1 +1,3 @@
         def test_add(): assert True
        +def test_add_char():
        +    assert True
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: Characterisation test
        **Status:** done
        ## Acceptance criteria
        - [x] Done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    base_results = BaseTestResults(
        new_tests=["tests/test_calc.py::test_add_char"],
        passed_tests=["tests/test_calc.py::test_add_char"],
        failed_tests=[],
        runtime_seconds=0.15,
    )

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        base_test_results=base_results,
    )

    assert verdict.verdict == Verdict.HOLD
    assert verdict.is_hold()
    assert any(
        "Check 7 hold" in r and "tests/test_calc.py::test_add_char" in r for r in verdict.reasons
    )
    assert any("0.15s" in r for r in verdict.reasons)


def test_check7_pass_when_new_tests_fail_or_error_on_base():
    base_tree = {
        "src/calc.py": "def add(a, b): return a + b\n",
        "tests/test_calc.py": "def test_add(): assert True\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_calc.py b/tests/test_calc.py
        --- a/tests/test_calc.py
        +++ b/tests/test_calc.py
        @@ -1,1 +1,3 @@
         def test_add(): assert True
        +def test_multiply_new():
        +    assert True
    """).strip() + "\n"
    ticket_content = textwrap.dedent("""
        # 10: New feature
        **Status:** done
        ## Acceptance criteria
        - [x] Done
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    # The new test failed/errored on base (desired outcome)
    base_results = BaseTestResults(
        new_tests=["tests/test_calc.py::test_multiply_new"],
        passed_tests=[],
        failed_tests=["tests/test_calc.py::test_multiply_new"],
        runtime_seconds=0.25,
    )

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        base_test_results=base_results,
    )

    assert verdict.verdict == Verdict.PASS
    assert verdict.is_pass()
    assert any(
        "Check 7 pass" in r and "0.25s" in r for r in verdict.reasons
    )


def test_check7_precedence_fail_over_hold():
    base_tree = {
        "tests/test_sample.py": "def test_a(): assert True\n",
    }
    pr_diff = textwrap.dedent("""
        diff --git a/tests/test_sample.py b/tests/test_sample.py
        --- a/tests/test_sample.py
        +++ b/tests/test_sample.py
        @@ -1,1 +1,3 @@
         def test_a(): assert True
        +def test_new_char(): assert True
    """).strip() + "\n"
    # Ticket is in-progress (fails check 6), while new test passed on base (holds check 7)
    ticket_content = textwrap.dedent("""
        # 10: Incomplete
        **Status:** in-progress
        ## Acceptance criteria
        - [ ] Unfinished
    """)
    ticket = TicketParser().parse_text(ticket_content, filename="10-ticket.md")
    core = IntegrityCore()

    base_results = BaseTestResults(
        new_tests=["tests/test_sample.py::test_new_char"],
        passed_tests=["tests/test_sample.py::test_new_char"],
        failed_tests=[],
        runtime_seconds=0.10,
    )

    verdict = core.evaluate(
        base_tree=base_tree,
        pr_diff=pr_diff,
        ticket=ticket,
        base_test_results=base_results,
    )

    assert verdict.verdict == Verdict.FAIL
    assert verdict.is_fail()
    assert any("Check 6 fail" in r for r in verdict.reasons)
    assert any("Check 7 hold" in r for r in verdict.reasons)




def test_check_6_pr_that_changes_no_ticket_file_fails_with_a_clear_reason():
    # Slackbot PR #38 implemented ticket 35 but never touched its ticket file. The gate
    # verified some other, already-done ticket instead, passed, and auto-merged it,
    # leaving ticket 35 `ready-for-agent` on master with its claim stuck forever.
    base_tree = {"tests/test_sample.py": "def test_a(): assert True\n"}

    verdict = IntegrityCore().evaluate(base_tree=base_tree, pr_diff="", ticket="")

    assert verdict.verdict == Verdict.FAIL
    assert any("does not change any ticket file" in r for r in verdict.reasons)
    # One clear reason, not a pile of confusing ones about an empty ticket.
    assert not any("no acceptance criteria" in r for r in verdict.reasons)
