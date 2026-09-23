"""Tests for prompt assembly and ticket skill.

WHY THIS EXISTS
---------------
Phase 1 Spec §Implementation Decisions (Prompt) and Ticket 02 acceptance
criteria require:
1. A runner-agnostic ticket skill merged from the three target repos.
2. A pure prompt-assembly function taking (skill text, repo, ticket path).
3. The prompt assembly must never log or write the prompt to stdout/stderr.
4. The assembled prompt must contain the required orientation order and context.
5. Tests must be written and observed failing before implementation (tests-first).
"""
import logging

import pytest

from ticket_engine.prompt import assemble_prompt, load_ticket_skill


def test_prompt_assembly_includes_required_elements():
    skill_text = "# Ticket Skill\nRules and instructions."
    repo = "Slackbot"
    ticket_path = ".scratch/phase-1/issues/02-runner-agnostic-ticket-skill-and-prompt.md"

    prompt = assemble_prompt(skill_text, repo, ticket_path)

    # Must contain the ticket path
    assert ticket_path in prompt
    # Must contain the repo name
    assert repo in prompt
    # Must contain the skill text
    assert skill_text in prompt
    # Must instruct to read AGENTS.md, the ticket, ADRs (docs/adr/), and CONTEXT.md first
    assert "AGENTS.md" in prompt
    assert "CONTEXT.md" in prompt
    assert "docs/adr" in prompt or "ADR" in prompt


def test_prompt_assembly_never_logs_or_prints(capsys, caplog):
    skill_text = "SECRET_SKILL_INSTRUCTIONS_NEVER_LEAK"
    repo = "MyRepo"
    ticket_path = ".scratch/phase-1/issues/sample-ticket.md"

    with caplog.at_level(logging.DEBUG):
        prompt = assemble_prompt(skill_text, repo, ticket_path)

    assert prompt
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert skill_text not in caplog.text
    assert ticket_path not in caplog.text


def test_ticket_skill_document_exists_and_loads():
    skill = load_ticket_skill()
    assert isinstance(skill, str)
    assert len(skill.strip()) > 0


def test_ticket_skill_contains_shared_rules():
    skill = load_ticket_skill()

    # Orientation order
    assert "AGENTS.md" in skill
    assert "CONTEXT.md" in skill
    assert "docs/adr" in skill or "ADR" in skill
    assert "docstring" in skill

    # Tests first and never mute
    assert "Tests first" in skill or "tests first" in skill or "test first" in skill
    assert "fail" in skill  # watch it fail
    assert "mute" in skill or "muted" in skill
    assert "skip" in skill
    assert "xfail" in skill

    # Adversarial review
    assert "adversarial" in skill.lower()

    # One ticket per branch
    assert "ticket/" in skill

    # Dropping repo-specific commands in favour of repo's engine config
    assert "engine config" in skill.lower() or "config" in skill.lower()

    # Role rule: refer to roles, never to people
    assert "roles" in skill.lower()
    assert "people" in skill.lower()


def test_ticket_skill_shell_neutrality_and_no_hard_gh_dependency():
    skill = load_ticket_skill()

    # Should not have PowerShell-only without bash or explanation
    # If powershell code block is used, bash should also be used
    if "```powershell" in skill:
        assert "```bash" in skill or "```sh" in skill

    # No hard dependency on gh - provides alternative or fallback when gh is unavailable
    assert "gh" in skill
    assert "URL" in skill or "browser" in skill or "pull/new" in skill or "without gh" in skill.lower() or "if `gh` is unavailable" in skill.lower() or "if gh is not" in skill.lower()


def test_ticket_skill_jules_section_and_escalation_format():
    skill = load_ticket_skill()

    # Clearly marked "If you are a Jules worker" section
    assert "If you are a Jules worker" in skill or "Jules worker" in skill

    # Jules specifics: do not push, do not open or edit PRs, CI is watched, respond to red CI by fixing
    assert "do not push" in skill.lower() or "never push" in skill.lower()
    assert "do not open" in skill.lower() or "never open" in skill.lower() or "do not create" in skill.lower()
    assert "watched" in skill.lower()
    assert "red" in skill.lower() and "fix" in skill.lower()

    # Escalation brief format
    assert "Escalation" in skill
    assert "Attempt 1" in skill or "attempt" in skill.lower()
    assert "Failing output" in skill or "failing output" in skill.lower()
    assert "Decision needed" in skill or "decision needed" in skill.lower()


def test_load_ticket_skill_custom_path_and_missing(tmp_path):
    custom_skill = tmp_path / "CUSTOM_SKILL.md"
    custom_skill.write_text("Custom skill text for test", encoding="utf-8")

    loaded = load_ticket_skill(custom_skill)
    assert loaded == "Custom skill text for test"

    with pytest.raises(FileNotFoundError):
        load_ticket_skill(tmp_path / "nonexistent.md")


def test_prompt_assembly_normalizes_ticket_path():
    skill_text = "Some skill"
    repo = "owner/repo"
    ticket_path = r".scratch\phase-1\issues\02-sample.md"

    prompt = assemble_prompt(skill_text, repo, ticket_path)
    assert ".scratch/phase-1/issues/02-sample.md" in prompt
    assert r".scratch\phase-1" not in prompt


def test_assembled_prompt_sample_ticket():
    skill = load_ticket_skill()
    repo = "Slackbot"
    ticket_path = ".scratch/phase-1/issues/02-runner-agnostic-ticket-skill-and-prompt.md"

    prompt = assemble_prompt(skill, repo, ticket_path)

    assert "Slackbot" in prompt
    assert ".scratch/phase-1/issues/02-runner-agnostic-ticket-skill-and-prompt.md" in prompt
    assert "AGENTS.md" in prompt
    assert "CONTEXT.md" in prompt
    assert "docs/adr" in prompt
    assert "If you are a Jules worker" in prompt
    assert "Escalation" in prompt
