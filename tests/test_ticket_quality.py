"""Tests for landable tickets: authorised test deletions, green holds, and ticket lint.

WHY THIS EXISTS
---------------
ADR 0005. Slackbot ticket 49 told its worker to delete two superseded test
functions. Integrity check 2 fails any deleted test function, so no worker could
ever land that ticket. And when the gate *held* a PR it set its required status to
`pending`, so the developer could not merge a held PR either: the "developer approves
a hold" step of ADR 0001 had no mechanism.

Three changes, tested here from the outside:

1. A ticket may authorise deleting named tests with a `Deletes tests:` line. The gate
   reads that line from the **base branch's** copy of the ticket, so a worker cannot
   authorise its own deletion. Authorised deletions (and the assertions inside them)
   do not fail check 2.
2. A hold reports the gate's status as `success`, labels the PR `engine:hold`, and
   never enables auto-merge. The developer merges it by hand.
3. Ticket lint: a ticket that would fail the gate as written is reported and is not
   started by the dispatcher.

Faked: the GitHub client and the Jules client. Real: the parser, the integrity core,
the runner on real git repos, the dispatch core, the run report and the CLI dry run.
"""
from __future__ import annotations

import pathlib
import subprocess
from unittest.mock import MagicMock, patch

from ticket_engine.cli import main as cli_main
from ticket_engine.config import RepoConfig
from ticket_engine.dispatch import DispatchCore, StartTicketAction, WorldSnapshot
from ticket_engine.integrity import IntegrityCore
from ticket_engine.integrity_runner import run_integrity_gate
from ticket_engine.parser import Ticket, TicketParser
from ticket_engine.run_report import RunFacts, build_run_report
from ticket_engine.ticket_lint import lint_ticket

# --- parser -------------------------------------------------------------------------


def parse(text: str, number: int = 49, effort: str = "bom") -> Ticket:
    path = pathlib.Path(".scratch") / effort / "issues" / f"{number:02d}-t.md"
    return TicketParser().parse_text(text, filename=path.name, path=path)


def test_deletes_tests_line_is_parsed_bold_or_plain():
    bold = parse(
        "# 49: T\n**Status:** ready-for-agent\n"
        "**Deletes tests:** `tests/test_26.py::test_old_a`, tests/test_26.py::test_old_b\n"
    )
    assert bold.deletes_tests == ["tests/test_26.py::test_old_a", "tests/test_26.py::test_old_b"]
    plain = parse("# 49: T\nStatus: ready-for-agent\nDeletes tests: tests/t.py::test_x\n")
    assert plain.deletes_tests == ["tests/t.py::test_x"]
    assert parse("# 49: T\n**Status:** ready-for-agent\n").deletes_tests == []


# --- integrity core: check 2 honours the base ticket's authorisation ----------------

BASE_TEST_FILE = (
    "def test_old_a():\n    assert 1 == 1\n    assert 2 == 2\n\n\n"
    "def test_keep():\n    assert 3 == 3\n"
)
HEAD_TEST_FILE = "def test_keep():\n    assert 3 == 3\n"
DONE_TICKET = "# 49: T\n**Status:** done\n## Acceptance criteria\n- [x] x\n"
AUTHORISING_TICKET = (
    "# 49: T\n**Status:** ready-for-agent\n"
    "**Deletes tests:** tests/test_26.py::test_old_a\n## Acceptance criteria\n- [ ] x\n"
)


def evaluate(head_file: str, base_ticket: str | None, head_ticket: str = DONE_TICKET):
    base = {
        "tests/test_26.py": BASE_TEST_FILE,
        ".scratch/bom/issues/49-t.md": "placeholder",
    }
    head = {"tests/test_26.py": head_file, ".scratch/bom/issues/49-t.md": head_ticket}
    return IntegrityCore().evaluate(
        base_tree=base, pr_diff=head, ticket=head_ticket, base_ticket=base_ticket
    )


def test_deletion_authorised_on_base_passes_and_says_so():
    verdict = evaluate(HEAD_TEST_FILE, base_ticket=AUTHORISING_TICKET)
    assert verdict.is_pass(), verdict.reasons
    assert (
        "Check 2: deleted test(s) the ticket authorises on the base branch: "
        "tests/test_26.py::test_old_a" in verdict.reasons
    )


def test_unauthorised_deletion_still_fails():
    verdict = evaluate(HEAD_TEST_FILE, base_ticket=DONE_TICKET.replace("done", "ready-for-agent"))
    assert verdict.is_fail()
    assert any("Deleted test function 'test_old_a'" in r for r in verdict.reasons)


def test_worker_cannot_authorise_its_own_deletion():
    # The Deletes tests line appears only in the PR's copy of the ticket.
    head_ticket = DONE_TICKET.replace(
        "**Status:** done\n", "**Status:** done\n**Deletes tests:** tests/test_26.py::test_old_a\n"
    )
    verdict = evaluate(HEAD_TEST_FILE, base_ticket=None, head_ticket=head_ticket)
    assert verdict.is_fail()
    assert any("Deleted test function 'test_old_a'" in r for r in verdict.reasons)


def test_authorisation_covers_only_the_deleted_tests_own_assertions():
    # test_old_a is deleted (2 assertions, authorised) AND test_keep loses its assertion.
    weakened = "def test_keep():\n    pass\n"
    verdict = evaluate(weakened, base_ticket=AUTHORISING_TICKET)
    assert verdict.is_fail()
    assert any("lost 1 assertion(s)" in r for r in verdict.reasons), verdict.reasons


# --- runner: base ticket read from git, and holds are green -------------------------


def _git(repo: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def _commit(repo: pathlib.Path, msg: str) -> None:
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A")
    _git(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", msg)


def _deletion_repo(tmp_path: pathlib.Path, base_ticket: str, pr_ticket: str) -> pathlib.Path:
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    (repo / ".scratch/bom/issues").mkdir(parents=True)
    (repo / "tests/test_26.py").write_text(BASE_TEST_FILE, encoding="utf-8")
    (repo / ".scratch/bom/issues/49-t.md").write_text(base_ticket, encoding="utf-8")
    _git(repo, "init", "-q", "-b", "master")
    _commit(repo, "base")
    _git(repo, "checkout", "-q", "-b", "pr")
    (repo / "tests/test_26.py").write_text(HEAD_TEST_FILE, encoding="utf-8")
    (repo / ".scratch/bom/issues/49-t.md").write_text(pr_ticket, encoding="utf-8")
    _commit(repo, "pr")
    return repo


def test_gate_on_real_git_reads_the_authorisation_from_the_base_branch(tmp_path):
    done = AUTHORISING_TICKET.replace("ready-for-agent", "done").replace("- [ ]", "- [x]")
    repo = _deletion_repo(tmp_path, base_ticket=AUTHORISING_TICKET, pr_ticket=done)
    summary = tmp_path / "s.md"
    code = run_integrity_gate(repo_path=repo, base_ref="master", step_summary_path=summary)
    text = summary.read_text(encoding="utf-8")
    assert code == 0, text
    assert "PASS" in text
    assert "tests/test_26.py::test_old_a" in text


def test_gate_on_real_git_fails_when_only_the_pr_adds_the_authorisation(tmp_path):
    base = AUTHORISING_TICKET.replace("**Deletes tests:** tests/test_26.py::test_old_a\n", "")
    done = AUTHORISING_TICKET.replace("ready-for-agent", "done").replace("- [ ]", "- [x]")
    repo = _deletion_repo(tmp_path, base_ticket=base, pr_ticket=done)
    summary = tmp_path / "s.md"
    code = run_integrity_gate(repo_path=repo, base_ref="master", step_summary_path=summary)
    assert code == 1
    assert "Deleted test function 'test_old_a'" in summary.read_text(encoding="utf-8")


def _run_with_client(ticket_text: str, diff: str) -> MagicMock:
    client = MagicMock()
    with patch("ticket_engine.integrity_runner.GitHubClient", return_value=client), \
         patch("ticket_engine.integrity_runner.resolve_base_ref", side_effect=lambda _p, r: r), \
         patch("ticket_engine.integrity_runner.get_base_tree_from_git",
               return_value={"tests/test_a.py": "def test_a(): assert True\n"}), \
         patch("ticket_engine.integrity_runner.get_pr_diff_from_git", return_value=diff), \
         patch("ticket_engine.integrity_runner.find_ticket_content_from_git",
               return_value=ticket_text):
        run_integrity_gate(
            repo_path=pathlib.Path("."), base_ref="origin/master", token="t",
            repo_name="owner/repo", pr_number=7, head_sha="abc123",
        )
    return client


_TOUCH_TESTS = (
    "diff --git a/tests/test_a.py b/tests/test_a.py\n--- a/tests/test_a.py\n"
    "+++ b/tests/test_a.py\n@@ -1 +1,2 @@\n def test_a(): assert True\n+# more\n"
)
_HELD = "# 01: T\n**Status:** done\n**Auto-merge:** no\n## Acceptance criteria\n- [x] Done\n"
_PASSING = "# 01: T\n**Status:** done\n## Acceptance criteria\n- [x] Done\n"


def test_hold_is_green_labelled_and_never_auto_merged():
    client = _run_with_client(_HELD, _TOUCH_TESTS)
    status = client.set_commit_status.call_args.kwargs
    assert status["state"] == "success"
    assert status["description"].startswith("HOLD, merge by hand: ")
    assert len(status["description"]) <= 140
    client.add_issue_labels.assert_called_once_with(
        repo="owner/repo", issue_number=7, labels=["engine:hold"]
    )
    client.enable_auto_merge.assert_not_called()
    client.merge_pull_request.assert_not_called()
    client.disable_auto_merge.assert_called_once_with(repo="owner/repo", pr_number=7)
    client.remove_issue_label.assert_not_called()


def test_pass_and_fail_clear_a_stale_hold_label():
    passed = _run_with_client(_PASSING, _TOUCH_TESTS)
    passed.remove_issue_label.assert_called_once_with(
        repo="owner/repo", issue_number=7, label="engine:hold"
    )
    failed = _run_with_client(_PASSING.replace("done", "in-progress"), _TOUCH_TESTS)
    assert failed.set_commit_status.call_args.kwargs["state"] == "failure"
    failed.remove_issue_label.assert_called_once_with(
        repo="owner/repo", issue_number=7, label="engine:hold"
    )


# --- ticket lint --------------------------------------------------------------------

TICKET_49_SHAPE = """# 49: The BOM sheet uses the lab layout

**Status:** ready-for-agent

**Blocked by:** None

## Acceptance criteria

- [ ] **The two old layout tests are replaced, not muted.**
  `test_build_bom_workbook_content_and_numbers` and `test_build_bom_workbook_draft_header`
  in `tests/test_26_line_items_and_bom.py` assert the old layout. Delete those two
  functions and nothing else in that file.
- [ ] Layout test in `tests/test_49_bom_lab_layout.py`.
"""


def test_lint_flags_a_ticket_that_orders_an_unauthorised_test_deletion():
    findings = lint_ticket(parse(TICKET_49_SHAPE))
    assert findings == [
        "tells the worker to delete `test_build_bom_workbook_content_and_numbers`, but no "
        "`Deletes tests:` line lists it, so integrity check 2 would fail the PR",
        "tells the worker to delete `test_build_bom_workbook_draft_header`, but no "
        "`Deletes tests:` line lists it, so integrity check 2 would fail the PR",
    ]


def test_lint_accepts_the_same_ticket_once_it_authorises_the_deletions():
    fixed = TICKET_49_SHAPE.replace(
        "**Blocked by:** None\n",
        "**Blocked by:** None\n\n**Deletes tests:** "
        "tests/test_26_line_items_and_bom.py::test_build_bom_workbook_content_and_numbers, "
        "tests/test_26_line_items_and_bom.py::test_build_bom_workbook_draft_header\n",
    )
    assert lint_ticket(parse(fixed)) == []


def test_lint_ignores_negated_and_non_test_deletions():
    text = (
        "# 16: T\n**Status:** ready-for-agent\n## Acceptance criteria\n"
        "- [ ] Do not delete `test_keep_me`; fix it.\n"
        "- [ ] Delete the `(\"check\", \"test\")` trigger and `remove_buyer` stays.\n"
    )
    assert lint_ticket(parse(text, number=16)) == []


def test_lint_flags_malformed_deletes_entries_and_missing_criteria():
    text = "# 51: T\n**Status:** ready-for-agent\n**Deletes tests:** test_old_a\n"
    assert lint_ticket(parse(text, number=51)) == [
        "`Deletes tests:` entry `test_old_a` is not `<test file>.py::<test name>`",
        "has no acceptance-criteria checkboxes, so integrity check 6 has nothing to verify",
    ]


def test_lint_has_nothing_to_judge_on_a_ticket_with_no_text():
    record = Ticket(number=3, title="T", slug="t", status="ready-for-agent")
    assert lint_ticket(record) == []


def test_lint_only_judges_tickets_an_agent_could_claim():
    for status in ("done", "ready-for-developer", "in-progress", "blocked"):
        assert lint_ticket(parse(TICKET_49_SHAPE.replace("ready-for-agent", status))) == []


# --- dispatcher and reports -------------------------------------------------------


def test_dispatcher_does_not_start_a_ticket_with_lint_findings_but_starts_the_next():
    bad = parse(TICKET_49_SHAPE)
    good = parse(
        "# 50: Good\n**Status:** ready-for-agent\n## Acceptance criteria\n- [ ] x\n", number=50
    )
    result = DispatchCore().evaluate(
        WorldSnapshot(tickets=[bad, good], config=RepoConfig(concurrency=2))
    )
    started = [a.ticket.number for a in result.actions if isinstance(a, StartTicketAction)]
    assert started == [50]
    assert [t.number for t in result.lint_held] == [49]


def test_run_report_lists_ticket_problems():
    bad = parse(TICKET_49_SHAPE)
    facts = RunFacts(repo="owner/Slackbot", lint_findings={49: lint_ticket(bad)})
    report = build_run_report([bad], facts)
    assert "### 🧹 Tickets an agent cannot land as written (not started until fixed)" in report
    assert (
        "- 49 The BOM sheet uses the lab layout: tells the worker to delete "
        "`test_build_bom_workbook_content_and_numbers`" in report
    )


def test_live_dispatch_reports_lint_findings(tmp_path):
    from ticket_engine.live_dispatch import LiveDispatcher

    github = MagicMock()
    github.get_default_branch_sha.return_value = "sha"
    github.list_claim_branches.return_value = []
    github.get_repo_variable.return_value = None
    github.create_claim_branch.return_value = True
    jules = MagicMock()
    jules.count_recent_sessions.return_value = 0
    jules.list_sessions.return_value = []
    d = LiveDispatcher(repo="owner/Slackbot", github_client=github, jules_client=jules,
                       config=RepoConfig(), skill_text="# s")
    started = d.dispatch([parse(TICKET_49_SHAPE)])
    assert started == []
    jules.create_session.assert_not_called()
    assert 49 in d.last_run.lint_findings


def test_dry_run_prints_ticket_problems(tmp_path, capsys):
    issues = tmp_path / ".scratch" / "bom" / "issues"
    issues.mkdir(parents=True)
    (issues / "49-bom.md").write_text(TICKET_49_SHAPE, encoding="utf-8")
    assert cli_main(["--dry-run", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "Ticket problems (an agent cannot land these as written):" in out
    assert "49: tells the worker to delete `test_build_bom_workbook_draft_header`" in out
