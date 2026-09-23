"""Tests for bootstrap adopt mode — file side.

Covers ticket 10 acceptance criteria:
1. Legacy status migration (complete, completed → done; human-task → ready-for-developer).
2. Ticket skill replaced with pointer; tests-first script replaced with engine's.
3. Adds engine config, caller workflows, ticket template, AGENTS.md sections.
4. PII scan prints report; does not rewrite files.
5. Idempotent: second run changes nothing; developer-edited file diffed+reported.
6. Proven on fixture repos shaped like Slackbot, RBL, TDS-T8.
"""
from __future__ import annotations

import pathlib
import textwrap

import pytest

from ticket_engine.bootstrap import (
    AGENTS_MD_IMPLEMENTATION_MARKER,
    AGENTS_MD_ROLES_MARKER,
    AdoptInput,
    adopt,
    migrate_ticket_text,
    run_adopt,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ticket(status: str, bold: bool = False) -> str:
    if bold:
        return textwrap.dedent(f"""\
            # 01: Thing

            **What to build:** Something.

            **Blocked by:** None

            **Status:** {status}

            **Runner:** any

            **Auto-merge:** yes

            ## Acceptance criteria

            - [ ] Do the thing.

            ## Comments
            """)
    return textwrap.dedent(f"""\
        # 01: Thing

        **What to build:** Something.

        **Blocked by:** None

        Status: {status}

        Runner: any

        Auto-merge: yes

        ## Acceptance criteria

        - [ ] Do the thing.

        ## Comments
        """)


_OLD_SKILL_CONTENT = textwrap.dedent("""\
    ---
    name: slackbot-ticket
    description: Old repo-specific ticket skill.
    ---

    # Old ticket skill

    Step 1: Run `gh pr create`.
    Step 10: Run `gh pr checks --watch`.
    """)

_OLD_TESTS_FIRST_CONTENT = textwrap.dedent("""\
    #!/usr/bin/env python
    # Old tests-first script — repo-specific drift.
    import sys
    sys.exit(0)
    """)

_ENGINE_TESTS_FIRST_CONTENT = textwrap.dedent("""\
    # Engine canonical tests-first script.
    import sys
    sys.exit(0)
    """)

_OLD_AGENTS_MD = textwrap.dedent("""\
    # AGENTS.md

    ## Conventions

    Keep functions small.
    """)


def _base_input(**overrides) -> AdoptInput:
    defaults: dict = {
        "engine_version": "v0.1.0",
        "ticket_files": {},
        "skill_files": {},
        "tests_first_content": None,
        "canonical_tests_first_content": _ENGINE_TESTS_FIRST_CONTENT,
        "agents_md_content": None,
        "engine_config_content": None,
        "dispatch_workflow_content": None,
        "integrity_workflow_content": None,
        "ticket_template_content": None,
        "public_files": {},
    }
    defaults.update(overrides)
    return AdoptInput(**defaults)


# ---------------------------------------------------------------------------
# Criterion 1: legacy status migration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("legacy,expected", [
    ("complete", "done"),
    ("completed", "done"),
    ("human-task", "ready-for-developer"),
])
def test_legacy_status_replaced_in_plain_ticket(legacy, expected):
    text = _make_ticket(legacy, bold=False)
    result = migrate_ticket_text(text)
    assert f"Status: {expected}" in result
    assert legacy not in result.lower().replace("ready-for-developer", "")


@pytest.mark.parametrize("legacy,expected", [
    ("complete", "done"),
    ("completed", "done"),
    ("human-task", "ready-for-developer"),
])
def test_legacy_status_replaced_in_bold_ticket(legacy, expected):
    text = _make_ticket(legacy, bold=True)
    result = migrate_ticket_text(text)
    assert f"Status:** {expected}" in result or f"Status: {expected}" in result
    assert legacy not in result.lower().replace("ready-for-developer", "")


def test_valid_status_unchanged_by_migration():
    for status in ("ready-for-agent", "ready-for-developer", "in-progress", "blocked", "done"):
        text = _make_ticket(status)
        assert migrate_ticket_text(text) == text


def test_adopt_writes_migrated_ticket_files():
    ticket_content = _make_ticket("complete")
    inp = _base_input(ticket_files={".scratch/phase-1/issues/01-thing.md": ticket_content})
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert ".scratch/phase-1/issues/01-thing.md" in paths
    written = next(w for w in result.writes if w.path == ".scratch/phase-1/issues/01-thing.md")
    assert "done" in written.content
    assert "complete" not in written.content.lower()


def test_adopt_marks_already_migrated_ticket_as_unchanged():
    ticket_content = _make_ticket("done")
    inp = _base_input(ticket_files={".scratch/phase-1/issues/01-thing.md": ticket_content})
    result = adopt(inp)
    paths_written = [w.path for w in result.writes]
    assert ".scratch/phase-1/issues/01-thing.md" not in paths_written
    assert ".scratch/phase-1/issues/01-thing.md" in result.unchanged


def test_adopt_migrates_multiple_tickets_independently():
    inp = _base_input(ticket_files={
        ".scratch/phase-1/issues/01-a.md": _make_ticket("complete"),
        ".scratch/phase-1/issues/02-b.md": _make_ticket("done"),
        ".scratch/phase-1/issues/03-c.md": _make_ticket("human-task"),
    })
    result = adopt(inp)
    written = {w.path: w.content for w in result.writes}
    assert ".scratch/phase-1/issues/01-a.md" in written
    assert "done" in written[".scratch/phase-1/issues/01-a.md"]
    assert ".scratch/phase-1/issues/02-b.md" not in written
    assert ".scratch/phase-1/issues/03-c.md" in written
    assert "ready-for-developer" in written[".scratch/phase-1/issues/03-c.md"]


# ---------------------------------------------------------------------------
# Criterion 2: ticket skill and tests-first script replaced
# ---------------------------------------------------------------------------

def test_old_skill_file_replaced_with_pointer():
    inp = _base_input(skill_files={
        ".agents/skills/repo-ticket/SKILL.md": _OLD_SKILL_CONTENT,
    })
    result = adopt(inp)
    written = {w.path: w.content for w in result.writes}
    assert ".agents/skills/repo-ticket/SKILL.md" in written
    content = written[".agents/skills/repo-ticket/SKILL.md"]
    # Must point to engine, not contain old step-by-step instructions
    assert "ticket-engine" in content
    assert "gh pr create" not in content
    assert "gh pr checks --watch" not in content


def test_skill_already_a_pointer_is_unchanged():
    from ticket_engine.bootstrap import TICKET_SKILL_POINTER
    inp = _base_input(skill_files={
        ".agents/skills/repo-ticket/SKILL.md": TICKET_SKILL_POINTER,
    })
    result = adopt(inp)
    written_paths = [w.path for w in result.writes]
    assert ".agents/skills/repo-ticket/SKILL.md" not in written_paths
    assert ".agents/skills/repo-ticket/SKILL.md" in result.unchanged


def test_missing_tests_first_script_is_written():
    inp = _base_input(tests_first_content=None)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert "scripts/check_tests_first.py" in paths
    written = next(w for w in result.writes if w.path == "scripts/check_tests_first.py")
    assert written.content == _ENGINE_TESTS_FIRST_CONTENT


def test_old_tests_first_script_replaced_with_engine_version():
    inp = _base_input(tests_first_content=_OLD_TESTS_FIRST_CONTENT)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert "scripts/check_tests_first.py" in paths
    written = next(w for w in result.writes if w.path == "scripts/check_tests_first.py")
    assert written.content == _ENGINE_TESTS_FIRST_CONTENT


def test_engine_tests_first_script_already_present_is_unchanged():
    inp = _base_input(tests_first_content=_ENGINE_TESTS_FIRST_CONTENT)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert "scripts/check_tests_first.py" not in paths
    assert "scripts/check_tests_first.py" in result.unchanged


# ---------------------------------------------------------------------------
# Criterion 3: engine config, caller workflows, ticket template, AGENTS.md sections
# ---------------------------------------------------------------------------

def test_engine_config_written_when_absent():
    inp = _base_input(engine_config_content=None)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert ".ticket-engine.toml" in paths


def test_engine_config_already_present_and_unchanged():
    from ticket_engine.bootstrap import ENGINE_CONFIG_TEMPLATE
    inp = _base_input(engine_config_content=ENGINE_CONFIG_TEMPLATE)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert ".ticket-engine.toml" not in paths
    assert ".ticket-engine.toml" in result.unchanged


def test_engine_config_developer_edited_is_diffed_not_overwritten():
    customized = "[tool.ticket-engine]\ndaily_cap = 20\nconcurrency = 3\n"
    inp = _base_input(engine_config_content=customized)
    result = adopt(inp)
    diff_paths = [d.path for d in result.diffs]
    written_paths = [w.path for w in result.writes]
    assert ".ticket-engine.toml" in diff_paths
    assert ".ticket-engine.toml" not in written_paths


def test_caller_dispatch_workflow_written_when_absent():
    inp = _base_input(dispatch_workflow_content=None)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert ".github/workflows/dispatch.yml" in paths
    content = next(w.content for w in result.writes if w.path == ".github/workflows/dispatch.yml")
    assert "v0.1.0" in content
    assert "ticket-engine" in content


def test_caller_integrity_workflow_written_when_absent():
    inp = _base_input(integrity_workflow_content=None)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert ".github/workflows/integrity.yml" in paths
    content = next(w.content for w in result.writes if w.path == ".github/workflows/integrity.yml")
    assert "v0.1.0" in content
    assert "ticket-engine" in content


def test_caller_workflow_developer_edited_is_diffed_not_overwritten():
    customized = "name: Custom Dispatch\non:\n  push:\n    branches: [main]\n"
    inp = _base_input(dispatch_workflow_content=customized)
    result = adopt(inp)
    diff_paths = [d.path for d in result.diffs]
    written_paths = [w.path for w in result.writes]
    assert ".github/workflows/dispatch.yml" in diff_paths
    assert ".github/workflows/dispatch.yml" not in written_paths


def test_ticket_template_written_with_runner_and_automerge():
    inp = _base_input(ticket_template_content=None)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert ".github/ISSUE_TEMPLATE/ticket.md" in paths
    content = next(w.content for w in result.writes if w.path == ".github/ISSUE_TEMPLATE/ticket.md")
    assert "Runner:" in content
    assert "Auto-merge:" in content
    assert "Status:" in content


def test_agents_md_sections_added_when_absent():
    inp = _base_input(agents_md_content=_OLD_AGENTS_MD)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert "AGENTS.md" in paths
    content = next(w.content for w in result.writes if w.path == "AGENTS.md")
    # Original content preserved
    assert "## Conventions" in content
    assert "Keep functions small." in content
    # New sections added
    assert AGENTS_MD_IMPLEMENTATION_MARKER in content
    assert AGENTS_MD_ROLES_MARKER in content


def test_agents_md_sections_absent_agents_md_created():
    inp = _base_input(agents_md_content=None)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert "AGENTS.md" in paths
    content = next(w.content for w in result.writes if w.path == "AGENTS.md")
    assert AGENTS_MD_IMPLEMENTATION_MARKER in content
    assert AGENTS_MD_ROLES_MARKER in content


def test_agents_md_sections_already_present_unchanged():
    from ticket_engine.bootstrap import AGENTS_MD_IMPLEMENTATION_SECTION, AGENTS_MD_ROLES_SECTION
    existing = _OLD_AGENTS_MD + "\n" + AGENTS_MD_IMPLEMENTATION_SECTION + "\n" + AGENTS_MD_ROLES_SECTION
    inp = _base_input(agents_md_content=existing)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert "AGENTS.md" not in paths
    assert "AGENTS.md" in result.unchanged


# ---------------------------------------------------------------------------
# Criterion 4: PII scan — report only, no rewrite
# ---------------------------------------------------------------------------

def test_pii_scan_finds_email_address():
    inp = _base_input(public_files={
        "docs/setup.md": "Contact developer@example.com for access.\n",
    })
    result = adopt(inp)
    email_findings = [f for f in result.pii_findings if f.pattern_name == "email"]
    assert len(email_findings) >= 1
    assert any(f.path == "docs/setup.md" for f in email_findings)
    assert any(f.line_number == 1 for f in email_findings)


def test_pii_scan_finds_slack_user_id():
    inp = _base_input(public_files={
        "docs/setup.md": "Ping <@U01AB2CD3EF> on Slack.\n",
    })
    result = adopt(inp)
    slack_findings = [f for f in result.pii_findings if f.pattern_name == "slack_id"]
    assert len(slack_findings) >= 1
    assert any(f.path == "docs/setup.md" for f in slack_findings)


def test_pii_scan_does_not_rewrite_files():
    email_doc = "docs/setup.md"
    original_content = "Contact developer@example.com for access.\n"
    inp = _base_input(public_files={email_doc: original_content})
    result = adopt(inp)
    # No write for docs/setup.md (PII files are reported, not rewritten)
    written_paths = [w.path for w in result.writes]
    assert email_doc not in written_paths


def test_pii_scan_never_echoes_matched_text():
    """PiiFinding.pattern_name never contains the actual matched email or Slack ID."""
    inp = _base_input(public_files={
        "docs/people.md": "Email: john.doe@privatelab.com\nSlack: <@U99XYZ12345>\n",
    })
    result = adopt(inp)
    for finding in result.pii_findings:
        assert "john.doe@privatelab.com" not in finding.pattern_name
        assert "U99XYZ12345" not in finding.pattern_name
        assert "@" not in finding.pattern_name


def test_clean_file_has_no_pii_findings():
    inp = _base_input(public_files={
        "docs/setup.md": "Contact the developer for access.\nSee the approver for sign-off.\n",
    })
    result = adopt(inp)
    assert result.pii_findings == []


# ---------------------------------------------------------------------------
# Criterion 5: idempotency
# ---------------------------------------------------------------------------

def test_second_run_changes_nothing():
    """Run adopt twice on the same input; second result has no writes."""
    from ticket_engine.bootstrap import (
        ENGINE_CONFIG_TEMPLATE,
    )

    engine_version = "v0.1.0"
    # First run — start from empty
    inp1 = _base_input(engine_version=engine_version)
    result1 = adopt(inp1)

    # Build "after first run" state
    written1 = {w.path: w.content for w in result1.writes}

    # Second run — pass in what the first run wrote
    agents_md_after = written1.get("AGENTS.md")
    inp2 = _base_input(
        engine_version=engine_version,
        engine_config_content=written1.get(".ticket-engine.toml", ENGINE_CONFIG_TEMPLATE),
        dispatch_workflow_content=written1.get(".github/workflows/dispatch.yml"),
        integrity_workflow_content=written1.get(".github/workflows/integrity.yml"),
        ticket_template_content=written1.get(".github/ISSUE_TEMPLATE/ticket.md"),
        agents_md_content=agents_md_after,
        tests_first_content=written1.get("scripts/check_tests_first.py", _ENGINE_TESTS_FIRST_CONTENT),
        canonical_tests_first_content=_ENGINE_TESTS_FIRST_CONTENT,
    )
    result2 = adopt(inp2)
    assert result2.writes == [], f"Second run had unexpected writes: {[w.path for w in result2.writes]}"
    assert result2.diffs == []


def test_developer_edited_engine_config_diffed_not_overwritten():
    from ticket_engine.bootstrap import ENGINE_CONFIG_TEMPLATE
    developer_edit = ENGINE_CONFIG_TEMPLATE + "\n# developer note\nconcurrency = 4\n"
    inp = _base_input(engine_config_content=developer_edit)
    result = adopt(inp)
    assert any(d.path == ".ticket-engine.toml" for d in result.diffs)
    assert not any(w.path == ".ticket-engine.toml" for w in result.writes)
    diff = next(d for d in result.diffs if d.path == ".ticket-engine.toml")
    assert diff.actual == developer_edit
    assert diff.expected == ENGINE_CONFIG_TEMPLATE


# ---------------------------------------------------------------------------
# Criterion 6: fixture repos (Slackbot, RBL, TDS-T8 shapes)
# ---------------------------------------------------------------------------

def _slackbot_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """Fixture shaped like Slackbot: bold statuses, complete/human-task, old skill."""
    (tmp_path / ".scratch" / "phase-1" / "issues").mkdir(parents=True)
    (tmp_path / ".scratch" / "phase-1" / "issues" / "01-setup.md").write_text(
        _make_ticket("complete", bold=True), encoding="utf-8"
    )
    (tmp_path / ".scratch" / "phase-1" / "issues" / "02-feature.md").write_text(
        _make_ticket("human-task", bold=True), encoding="utf-8"
    )
    (tmp_path / ".agents" / "skills" / "slackbot-ticket").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "slackbot-ticket" / "SKILL.md").write_text(
        _OLD_SKILL_CONTENT, encoding="utf-8"
    )
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "scripts" / "check_tests_first.py").write_text(
        _OLD_TESTS_FIRST_CONTENT, encoding="utf-8"
    )
    (tmp_path / "AGENTS.md").write_text(_OLD_AGENTS_MD, encoding="utf-8")
    return tmp_path


def _rbl_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """Fixture shaped like RBL: completed + valid statuses, rbl-ticket skill."""
    (tmp_path / ".scratch" / "phase-1" / "issues").mkdir(parents=True)
    (tmp_path / ".scratch" / "phase-1" / "issues" / "01-setup.md").write_text(
        _make_ticket("completed", bold=True), encoding="utf-8"
    )
    (tmp_path / ".scratch" / "phase-1" / "issues" / "02-review.md").write_text(
        _make_ticket("ready-for-developer"), encoding="utf-8"
    )
    (tmp_path / ".agents" / "skills" / "rbl-ticket").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "rbl-ticket" / "SKILL.md").write_text(
        _OLD_SKILL_CONTENT, encoding="utf-8"
    )
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "scripts" / "check_tests_first.py").write_text(
        _OLD_TESTS_FIRST_CONTENT, encoding="utf-8"
    )
    return tmp_path


def _tds_t8_fixture(tmp_path: pathlib.Path) -> pathlib.Path:
    """Fixture shaped like TDS-T8: complete + in-progress, Windows CI, old skill."""
    (tmp_path / ".scratch" / "phase-1" / "issues").mkdir(parents=True)
    (tmp_path / ".scratch" / "phase-1" / "issues" / "01-setup.md").write_text(
        _make_ticket("complete", bold=True), encoding="utf-8"
    )
    (tmp_path / ".scratch" / "phase-1" / "issues" / "02-work.md").write_text(
        _make_ticket("in-progress"), encoding="utf-8"
    )
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "ci.yml").write_text(
        "name: CI\non:\n  push:\njobs:\n  test:\n    runs-on: windows-latest\n    steps: []\n",
        encoding="utf-8",
    )
    (tmp_path / ".agents" / "skills" / "tds-ticket").mkdir(parents=True)
    (tmp_path / ".agents" / "skills" / "tds-ticket" / "SKILL.md").write_text(
        _OLD_SKILL_CONTENT, encoding="utf-8"
    )
    (tmp_path / "scripts").mkdir(parents=True)
    (tmp_path / "scripts" / "check_tests_first.py").write_text(
        _OLD_TESTS_FIRST_CONTENT, encoding="utf-8"
    )
    return tmp_path


def test_slackbot_fixture_statuses_migrated(tmp_path):
    repo = _slackbot_fixture(tmp_path)
    run_adopt(repo, engine_version="v0.1.0",
              canonical_tests_first_content=_ENGINE_TESTS_FIRST_CONTENT)
    ticket_01 = (repo / ".scratch" / "phase-1" / "issues" / "01-setup.md").read_text(encoding="utf-8")
    ticket_02 = (repo / ".scratch" / "phase-1" / "issues" / "02-feature.md").read_text(encoding="utf-8")
    assert "Status:** done" in ticket_01 or "Status: done" in ticket_01
    assert "complete" not in ticket_01.lower()
    assert "ready-for-developer" in ticket_02
    assert "human-task" not in ticket_02


def test_slackbot_fixture_skill_replaced(tmp_path):
    repo = _slackbot_fixture(tmp_path)
    run_adopt(repo, engine_version="v0.1.0",
              canonical_tests_first_content=_ENGINE_TESTS_FIRST_CONTENT)
    skill_content = (repo / ".agents" / "skills" / "slackbot-ticket" / "SKILL.md").read_text(encoding="utf-8")
    assert "ticket-engine" in skill_content
    assert "gh pr create" not in skill_content


def test_slackbot_fixture_tests_first_replaced(tmp_path):
    repo = _slackbot_fixture(tmp_path)
    run_adopt(repo, engine_version="v0.1.0",
              canonical_tests_first_content=_ENGINE_TESTS_FIRST_CONTENT)
    content = (repo / "scripts" / "check_tests_first.py").read_text(encoding="utf-8")
    assert content == _ENGINE_TESTS_FIRST_CONTENT


def test_slackbot_fixture_engine_config_added(tmp_path):
    repo = _slackbot_fixture(tmp_path)
    run_adopt(repo, engine_version="v0.1.0",
              canonical_tests_first_content=_ENGINE_TESTS_FIRST_CONTENT)
    assert (repo / ".ticket-engine.toml").exists()


def test_slackbot_fixture_caller_workflows_added_with_version(tmp_path):
    repo = _slackbot_fixture(tmp_path)
    run_adopt(repo, engine_version="v0.1.0",
              canonical_tests_first_content=_ENGINE_TESTS_FIRST_CONTENT)
    dispatch = (repo / ".github" / "workflows" / "dispatch.yml").read_text(encoding="utf-8")
    integrity = (repo / ".github" / "workflows" / "integrity.yml").read_text(encoding="utf-8")
    assert "v0.1.0" in dispatch
    assert "ticket-engine" in dispatch
    assert "v0.1.0" in integrity
    assert "ticket-engine" in integrity


def test_slackbot_fixture_ticket_template_added(tmp_path):
    repo = _slackbot_fixture(tmp_path)
    run_adopt(repo, engine_version="v0.1.0",
              canonical_tests_first_content=_ENGINE_TESTS_FIRST_CONTENT)
    template = (repo / ".github" / "ISSUE_TEMPLATE" / "ticket.md").read_text(encoding="utf-8")
    assert "Runner:" in template
    assert "Auto-merge:" in template


def test_slackbot_fixture_agents_md_sections_added(tmp_path):
    repo = _slackbot_fixture(tmp_path)
    run_adopt(repo, engine_version="v0.1.0",
              canonical_tests_first_content=_ENGINE_TESTS_FIRST_CONTENT)
    agents_md = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert AGENTS_MD_IMPLEMENTATION_MARKER in agents_md
    assert AGENTS_MD_ROLES_MARKER in agents_md
    # Original content preserved
    assert "Keep functions small." in agents_md


def test_rbl_fixture_statuses_migrated(tmp_path):
    repo = _rbl_fixture(tmp_path)
    run_adopt(repo, engine_version="v0.1.0",
              canonical_tests_first_content=_ENGINE_TESTS_FIRST_CONTENT)
    ticket_01 = (repo / ".scratch" / "phase-1" / "issues" / "01-setup.md").read_text(encoding="utf-8")
    ticket_02 = (repo / ".scratch" / "phase-1" / "issues" / "02-review.md").read_text(encoding="utf-8")
    assert "done" in ticket_01
    assert "completed" not in ticket_01.lower()
    # valid status preserved
    assert "ready-for-developer" in ticket_02


def test_tds_t8_fixture_statuses_migrated_windows_ci_preserved(tmp_path):
    repo = _tds_t8_fixture(tmp_path)
    run_adopt(repo, engine_version="v0.1.0",
              canonical_tests_first_content=_ENGINE_TESTS_FIRST_CONTENT)
    ticket_01 = (repo / ".scratch" / "phase-1" / "issues" / "01-setup.md").read_text(encoding="utf-8")
    assert "done" in ticket_01
    assert "complete" not in ticket_01.lower()
    # Windows CI workflow untouched
    ci = (repo / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "windows-latest" in ci


def test_second_run_on_slackbot_fixture_changes_nothing(tmp_path):
    repo = _slackbot_fixture(tmp_path)
    kwargs: dict = {"engine_version": "v0.1.0", "canonical_tests_first_content": _ENGINE_TESTS_FIRST_CONTENT}
    run_adopt(repo, **kwargs)
    result2 = run_adopt(repo, **kwargs)
    assert result2.writes == [], f"Second run wrote: {[w.path for w in result2.writes]}"
    assert result2.diffs == []
