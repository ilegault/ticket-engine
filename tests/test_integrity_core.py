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
    IntegrityConfig,
    IntegrityCore,
    IntegrityVerdict,
    Verdict,
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
