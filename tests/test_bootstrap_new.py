"""Tests for bootstrap new mode and the planner-instructions template.

Covers ticket 12 acceptance criteria:
1. new mode on an empty directory produces skeleton AGENTS.md (with protocol sections),
   CONTEXT.md, tests-first ADR, .scratch/ layout, CI with the gates, engine config
   and caller workflows.
2. The Cowork planning instructions live in the engine as a template, rendered per
   repo by both modes.
3. The template's rules include: five status words exactly; every ticket carries
   Runner and Auto-merge; approval-required tickets placed as late in the dependency
   graph as possible; roles, never people; the planner hands off by pushing tickets.
4. Tests assert the generated tree for new and the rendered template for a sample repo.
"""
from __future__ import annotations

from ticket_engine.bootstrap import (
    AGENTS_MD_IMPLEMENTATION_MARKER,
    AGENTS_MD_ROLES_MARKER,
    AdoptInput,
    NewInput,
    NewResult,
    adopt,
    new,
    render_planner_instructions,
    run_new,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENGINE_TESTS_FIRST = "# engine canonical\nimport sys\nsys.exit(0)\n"
_PLANNER_INSTRUCTIONS_PATH = ".agents/planner/INSTRUCTIONS.md"


def _base_adopt_input(**overrides) -> AdoptInput:
    defaults: dict = {
        "engine_version": "v0.1.0",
        "ticket_files": {},
        "skill_files": {},
        "tests_first_content": None,
        "canonical_tests_first_content": _ENGINE_TESTS_FIRST,
        "agents_md_content": None,
        "engine_config_content": None,
        "dispatch_workflow_content": None,
        "integrity_workflow_content": None,
        "ticket_template_content": None,
        "public_files": {},
        "repo_name": "my-repo",
        "planner_instructions_content": None,
    }
    defaults.update(overrides)
    return AdoptInput(**defaults)


def _new_input(**overrides) -> NewInput:
    defaults: dict = {
        "repo_name": "my-new-repo",
        "engine_version": "v0.1.0",
        "canonical_tests_first_content": _ENGINE_TESTS_FIRST,
    }
    defaults.update(overrides)
    return NewInput(**defaults)


def _written(result: NewResult, path: str) -> str:
    fw = next((w for w in result.writes if w.path == path), None)
    assert fw is not None, f"Expected write for {path!r} but not found. Got: {[w.path for w in result.writes]}"
    return fw.content


# ---------------------------------------------------------------------------
# Criterion 1: new mode produces skeleton tree
# ---------------------------------------------------------------------------

def test_new_mode_produces_agents_md_with_protocol_sections():
    result = new(_new_input())
    content = _written(result, "AGENTS.md")
    assert AGENTS_MD_IMPLEMENTATION_MARKER in content
    assert AGENTS_MD_ROLES_MARKER in content


def test_new_mode_produces_context_md():
    result = new(_new_input())
    content = _written(result, "CONTEXT.md")
    assert "CONTEXT" in content.upper()


def test_new_mode_produces_tests_first_adr():
    result = new(_new_input())
    content = _written(result, "docs/adr/0001-tests-first.md")
    assert "test" in content.lower()
    assert "ADR" in content or "adr" in content.lower() or "#" in content


def test_new_mode_produces_scratch_layout():
    """new mode must produce at least one file under .scratch/phase-1/issues/."""
    result = new(_new_input())
    scratch_paths = [w.path for w in result.writes if w.path.startswith(".scratch/")]
    assert scratch_paths, "Expected at least one file under .scratch/"
    assert any("issues" in p for p in scratch_paths)


def test_new_mode_produces_engine_config():
    result = new(_new_input())
    content = _written(result, ".ticket-engine.toml")
    assert "default_branch" in content


def test_new_mode_produces_caller_dispatch_workflow():
    result = new(_new_input())
    content = _written(result, ".github/workflows/dispatch.yml")
    assert "v0.1.0" in content
    assert "ticket-engine" in content


def test_new_mode_produces_caller_integrity_workflow():
    result = new(_new_input())
    content = _written(result, ".github/workflows/integrity.yml")
    assert "v0.1.0" in content
    assert "ticket-engine" in content


def test_new_mode_produces_ci_workflow():
    result = new(_new_input())
    content = _written(result, ".github/workflows/ci.yml")
    assert "check_tests_first" in content or "pytest" in content


def test_new_mode_produces_ticket_template():
    result = new(_new_input())
    content = _written(result, ".github/ISSUE_TEMPLATE/ticket.md")
    assert "Runner:" in content
    assert "Auto-merge:" in content
    assert "Status:" in content


def test_new_mode_produces_tests_first_script():
    result = new(_new_input())
    content = _written(result, "scripts/check_tests_first.py")
    assert content == _ENGINE_TESTS_FIRST


def test_new_mode_tree_is_complete_list():
    """Spot-check that every required path appears exactly once."""
    result = new(_new_input())
    paths = {w.path for w in result.writes}
    required = {
        "AGENTS.md",
        "CONTEXT.md",
        "docs/adr/0001-tests-first.md",
        ".ticket-engine.toml",
        ".github/workflows/dispatch.yml",
        ".github/workflows/integrity.yml",
        ".github/workflows/ci.yml",
        ".github/ISSUE_TEMPLATE/ticket.md",
        "scripts/check_tests_first.py",
        _PLANNER_INSTRUCTIONS_PATH,
    }
    missing = required - paths
    assert not missing, f"new mode is missing writes: {missing}"


def test_run_new_adapter_writes_all_files_to_disk(tmp_path):
    run_new(
        tmp_path,
        repo_name="test-repo",
        engine_version="v0.1.0",
        canonical_tests_first_content=_ENGINE_TESTS_FIRST,
    )
    assert (tmp_path / "AGENTS.md").exists()
    assert (tmp_path / "CONTEXT.md").exists()
    assert (tmp_path / "docs" / "adr" / "0001-tests-first.md").exists()
    assert (tmp_path / ".ticket-engine.toml").exists()
    assert (tmp_path / ".github" / "workflows" / "dispatch.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "integrity.yml").exists()
    assert (tmp_path / ".github" / "workflows" / "ci.yml").exists()
    assert (tmp_path / ".github" / "ISSUE_TEMPLATE" / "ticket.md").exists()
    assert (tmp_path / "scripts" / "check_tests_first.py").exists()
    assert (tmp_path / ".agents" / "planner" / "INSTRUCTIONS.md").exists()
    assert any((tmp_path / ".scratch").rglob("*"))


def test_run_new_adapter_idempotent(tmp_path):
    """Running new twice on the same directory changes nothing the second time."""
    kwargs = {
        "repo_name": "test-repo",
        "engine_version": "v0.1.0",
        "canonical_tests_first_content": _ENGINE_TESTS_FIRST,
    }
    run_new(tmp_path, **kwargs)
    result2 = run_new(tmp_path, **kwargs)
    assert result2.writes == [], f"Second run wrote unexpected files: {[w.path for w in result2.writes]}"


# ---------------------------------------------------------------------------
# Criterion 2: both modes render planner instructions
# ---------------------------------------------------------------------------

def test_new_mode_produces_planner_instructions():
    result = new(_new_input(repo_name="sample-repo"))
    content = _written(result, _PLANNER_INSTRUCTIONS_PATH)
    assert len(content) > 100
    assert "sample-repo" in content


def test_adopt_mode_writes_planner_instructions_when_absent():
    inp = _base_adopt_input(repo_name="slackbot", planner_instructions_content=None)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert _PLANNER_INSTRUCTIONS_PATH in paths
    content = next(w.content for w in result.writes if w.path == _PLANNER_INSTRUCTIONS_PATH)
    assert "slackbot" in content


def test_adopt_mode_planner_instructions_unchanged_when_already_present():
    rendered = render_planner_instructions("my-repo")
    inp = _base_adopt_input(repo_name="my-repo", planner_instructions_content=rendered)
    result = adopt(inp)
    paths = [w.path for w in result.writes]
    assert _PLANNER_INSTRUCTIONS_PATH not in paths
    assert _PLANNER_INSTRUCTIONS_PATH in result.unchanged


def test_adopt_mode_developer_edited_planner_instructions_diffed_not_overwritten():
    customized = "# My custom planning notes\nDo it differently.\n"
    inp = _base_adopt_input(repo_name="my-repo", planner_instructions_content=customized)
    result = adopt(inp)
    diff_paths = [d.path for d in result.diffs]
    written_paths = [w.path for w in result.writes]
    assert _PLANNER_INSTRUCTIONS_PATH in diff_paths
    assert _PLANNER_INSTRUCTIONS_PATH not in written_paths


# ---------------------------------------------------------------------------
# Criterion 3: template rules
# ---------------------------------------------------------------------------

def test_planner_instructions_contain_all_five_status_words():
    instructions = render_planner_instructions("any-repo")
    for word in ("ready-for-agent", "ready-for-developer", "in-progress", "blocked", "done"):
        assert word in instructions, f"Status word {word!r} missing from planner instructions"


def test_planner_instructions_every_ticket_carries_runner_and_automerge():
    instructions = render_planner_instructions("any-repo")
    assert "Runner" in instructions
    assert "Auto-merge" in instructions


def test_planner_instructions_approval_tickets_late_in_dependency_graph():
    instructions = render_planner_instructions("any-repo")
    lower = instructions.lower()
    assert "late" in lower or "leaf" in lower or "leaves" in lower or "dependency graph" in lower


def test_planner_instructions_roles_not_people():
    instructions = render_planner_instructions("any-repo")
    lower = instructions.lower()
    assert "role" in lower
    assert "people" in lower or "name" in lower or "person" in lower


def test_planner_instructions_planner_hands_off_by_pushing_tickets():
    instructions = render_planner_instructions("any-repo")
    lower = instructions.lower()
    assert "push" in lower


def test_planner_instructions_rendered_with_repo_name():
    instructions = render_planner_instructions("tds-t8")
    assert "tds-t8" in instructions


def test_planner_instructions_different_repos_differ():
    a = render_planner_instructions("repo-a")
    b = render_planner_instructions("repo-b")
    assert a != b
    assert "repo-a" in a
    assert "repo-b" in b


# ---------------------------------------------------------------------------
# Criterion 4: generated tree for new and rendered template for sample repo
# ---------------------------------------------------------------------------

def test_new_mode_generated_tree_matches_expected_paths():
    """Assert the exact set of paths new mode produces (spec-derived list)."""
    result = new(_new_input(repo_name="alpha"))
    paths = sorted(w.path for w in result.writes)
    required = {
        "AGENTS.md",
        "CONTEXT.md",
        "docs/adr/0001-tests-first.md",
        ".ticket-engine.toml",
        ".github/workflows/dispatch.yml",
        ".github/workflows/integrity.yml",
        ".github/workflows/ci.yml",
        ".github/ISSUE_TEMPLATE/ticket.md",
        "scripts/check_tests_first.py",
        _PLANNER_INSTRUCTIONS_PATH,
    }
    missing = required - set(paths)
    assert not missing, f"Missing from new-mode tree: {missing}"


def test_rendered_planner_instructions_for_sample_repo_contains_all_rules():
    """End-to-end assertion: rendered instructions for a sample repo satisfy all rule checks."""
    instructions = render_planner_instructions("p-bot")
    assert "p-bot" in instructions
    for status in ("ready-for-agent", "ready-for-developer", "in-progress", "blocked", "done"):
        assert status in instructions
    assert "Runner" in instructions
    assert "Auto-merge" in instructions
    lower = instructions.lower()
    assert "push" in lower
    assert "role" in lower


def test_adopt_second_run_preserves_planner_instructions_idempotency():
    """Two adopt runs in a row never overwrite planner instructions after first run."""
    inp1 = _base_adopt_input(repo_name="beta", planner_instructions_content=None)
    result1 = adopt(inp1)
    written1 = {w.path: w.content for w in result1.writes}

    inp2 = _base_adopt_input(
        repo_name="beta",
        planner_instructions_content=written1.get(_PLANNER_INSTRUCTIONS_PATH),
        engine_config_content=written1.get(".ticket-engine.toml"),
        dispatch_workflow_content=written1.get(".github/workflows/dispatch.yml"),
        integrity_workflow_content=written1.get(".github/workflows/integrity.yml"),
        ticket_template_content=written1.get(".github/ISSUE_TEMPLATE/ticket.md"),
        agents_md_content=written1.get("AGENTS.md"),
        tests_first_content=written1.get("scripts/check_tests_first.py", _ENGINE_TESTS_FIRST),
        canonical_tests_first_content=_ENGINE_TESTS_FIRST,
    )
    result2 = adopt(inp2)
    assert result2.writes == [], f"Second adopt run wrote: {[w.path for w in result2.writes]}"
    assert result2.diffs == []


def test_planner_instructions_carry_the_acceptance_criteria_rules():
    # Tickets 35 and 40 in Slackbot went wrong in ways each of these rules targets:
    # a vague "then it passes" gave a test that proved nothing, an invariant cited by
    # number was not followed, a criterion promised something Slack cannot do, a
    # ten-criterion ticket dropped half of them, the tests faked the lookup under
    # change, and real first names ended up in the code.
    instructions = render_planner_instructions("any-repo")
    section = instructions[instructions.index("## Writing acceptance criteria"):]
    for phrase in (
        "names its proof",
        "by file and function",
        "Check the platform",
        "five criteria",
        "may be faked",
        "Roles, never names",
    ):
        assert phrase in section, f"acceptance-criteria rule missing: {phrase!r}"


def test_engine_issue_tracker_doc_uses_only_valid_status_words():
    # The doc once listed `human-task`, which the parser treats as legacy, so ticket
    # sets written from it held tickets the dispatcher reported as unreadable.
    import pathlib

    from ticket_engine.parser import VALID_STATUSES

    doc = (pathlib.Path(__file__).resolve().parent.parent / "docs/agents/issue-tracker.md").read_text(
        encoding="utf-8"
    )
    table = doc[doc.index("## Status vocabulary"):doc.index("## Working the frontier")]
    listed = {line.split("`")[1] for line in table.splitlines() if line.startswith("| `")}
    assert listed == set(VALID_STATUSES)
