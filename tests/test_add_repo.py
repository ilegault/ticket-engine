"""Tests for ticket_engine.add_repo (ticket 63).

WHY THIS EXISTS
---------------
Ticket 63 adds the first slice of `add-repo`:
- CLI entry point `add-repo` with flags `--check`, `--no-jules`, `--secrets-file`, `--engine-repo`.
- `load_secrets_file` for reading KEY=VALUE lines without printing secrets.
- `gather_facts` collecting state via read-only `gh` calls.
- `plan_add_repo` pure function building the plan.
- `config_upgrade` pure text manipulation upgrading `.ticket-engine.toml`.
- `--check` mode printing the plan without taking actions.
"""
from __future__ import annotations

import pathlib
import tomllib
from unittest.mock import MagicMock

import pytest

from ticket_engine.add_repo import (
    ActionsPermissionsOp,
    RepoFacts,
    StepKind,
    config_upgrade,
    gather_facts,
    load_secrets_file,
    main,
    plan_add_repo,
)
from ticket_engine.bootstrap import (
    CreateLabelOp,
    CreateRulesetOp,
    EnableAutoMergeOp,
    EnablePushProtectionOp,
    EnableSecretScanningOp,
    SetSecretOp,
)
from ticket_engine.github import GitHubClient


class FakeGhRecorder:
    def __init__(self, responses: dict[tuple[str, ...], tuple[int, str]] | None = None) -> None:
        self.responses: dict[tuple[str, ...], tuple[int, str]] = dict(responses or {})
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args: list[str], stdin: str | None = None) -> tuple[int, str]:
        self.calls.append((list(args), stdin))
        key = tuple(args)
        if key in self.responses:
            return self.responses[key]
        return 0, ""


def _default_fully_wired_responses(repo: str, engine_repo: str) -> dict[tuple[str, ...], tuple[int, str]]:
    repo_name = repo.split("/")[-1]
    return {
        ("api", f"repos/{repo}"): (
            0,
            (
                '{"default_branch": "master", "allow_auto_merge": true, '
                '"security_and_analysis": {"secret_scanning": {"status": "enabled"}, '
                '"secret_scanning_push_protection": {"status": "enabled"}}}'
            ),
        ),
        ("api", f"repos/{repo}/actions/secrets", "--paginate", "--jq", ".secrets[].name"): (
            0,
            "JULES_API_KEY\nPIPELINE_TOKEN\n",
        ),
        ("api", f"repos/{repo}/labels", "--paginate", "--jq", ".[].name"): (
            0,
            "engine:hold\nengine:escalated\nengine:windows-waiting\n",
        ),
        ("api", f"repos/{repo}/rulesets", "--jq", ".[].name"): (
            0,
            "engine-branch-protection\n",
        ),
        ("api", f"repos/{repo}/actions/permissions"): (
            0,
            '{"enabled": true, "allowed_actions": "all"}',
        ),
        ("api", f"repos/{repo}/contents/.ticket-engine.toml", "-H", "Accept: application/vnd.github.raw"): (
            0,
            'default_branch = "master"\npython_version = "3.12"\ninstall = "pip install -e .[dev]"\njules_enabled = true\n',
        ),
        ("pr", "list", "--repo", repo, "--head", "engine/add-repo", "--state", "open", "--json", "number", "--jq", ".[0].number"): (
            0,
            "",
        ),
        ("api", f"repos/{engine_repo}/contents/engine-repos.toml", "-H", "Accept: application/vnd.github.raw"): (
            0,
            f'[[repos]]\nrepo = "{repo}"\n',
        ),
        ("pr", "list", "--repo", engine_repo, "--head", f"engine/add-{repo_name}", "--state", "open", "--json", "number", "--jq", ".[0].number"): (
            0,
            "",
        ),
    }


def _fully_wired_facts(repo: str = "owner/target") -> RepoFacts:
    return RepoFacts(
        repo=repo,
        default_branch="master",
        allow_auto_merge=True,
        secret_scanning_enabled=True,
        push_protection_enabled=True,
        existing_secrets=["JULES_API_KEY", "PIPELINE_TOKEN"],
        existing_labels=["engine:hold", "engine:escalated", "engine:windows-waiting"],
        existing_rulesets=["engine-branch-protection"],
        actions_enabled=True,
        allowed_actions="all",
        config_content=(
            'default_branch = "master"\npython_version = "3.12"\n'
            'install = "pip install -e .[dev]"\njules_enabled = true\n'
        ),
        adopt_pr_open=False,
        in_engine_repos=True,
        list_pr_open=False,
        pipeline_token_ok=True,
    )


# ===========================================================================
# 1. load_secrets_file and CLI argument / secrets handling
# ===========================================================================


def test_load_secrets_file_parses_comments_and_quotes(tmp_path: pathlib.Path) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text(
        "# Top comment\n"
        "\n"
        'PIPELINE_TOKEN="secret-pipeline-123"\n'
        "JULES_API_KEY='secret-jules-456'\n"
        "PLAIN_VAL=just_plain\n"
        "WITH_EQUALS=part1=part2\n"
        "   # indented comment\n"
        'SPACES_AROUND = "value_with_spaces"  \n',
        encoding="utf-8",
    )
    loaded = load_secrets_file(secrets_file)
    assert loaded["PIPELINE_TOKEN"] == "secret-pipeline-123"
    assert loaded["JULES_API_KEY"] == "secret-jules-456"
    assert loaded["PLAIN_VAL"] == "just_plain"
    assert loaded["WITH_EQUALS"] == "part1=part2"
    assert loaded["SPACES_AROUND"] == "value_with_spaces"


def test_missing_secrets_file_prints_path_and_returns_2(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    missing_file = tmp_path / "does_not_exist.env"
    code = main(["owner/repo", "--check", "--secrets-file", str(missing_file)])
    assert code == 2
    out = capsys.readouterr().out
    assert str(missing_file) in out


def test_secrets_file_without_pipeline_token_prints_path_and_returns_2(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("JULES_API_KEY=val\nSOME_SECRET=sensitive_xyz\n", encoding="utf-8")
    code = main(["owner/repo", "--check", "--secrets-file", str(secrets_file)])
    assert code == 2
    captured = capsys.readouterr()
    assert str(secrets_file) in captured.out
    assert "sensitive_xyz" not in captured.out
    assert "sensitive_xyz" not in captured.err


# ===========================================================================
# 2. gather_facts
# ===========================================================================


def test_gather_facts_makes_only_read_calls() -> None:
    repo = "owner/my-repo"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    gh = FakeGhRecorder(responses)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    facts = gather_facts(repo, engine_repo, gh, probe)

    assert facts.repo == repo
    assert facts.pipeline_token_ok is True
    assert facts.in_engine_repos is True
    assert facts.adopt_pr_open is False
    assert facts.list_pr_open is False

    # Check the invariant: every gh api call has no -X/--method, and every other call is gh pr list
    for args, _stdin in gh.calls:
        if args[0] == "api":
            assert "-X" not in args
            assert "--method" not in args
        else:
            assert args[:2] == ["pr", "list"]


def test_gather_facts_handles_404_config() -> None:
    repo = "owner/my-repo"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    responses[("api", f"repos/{repo}/contents/.ticket-engine.toml", "-H", "Accept: application/vnd.github.raw")] = (
        1,
        "404: Not Found",
    )
    gh = FakeGhRecorder(responses)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    facts = gather_facts(repo, engine_repo, gh, probe)
    assert facts.config_content is None


def test_gather_facts_detects_open_prs() -> None:
    repo = "owner/my-repo"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    responses[("pr", "list", "--repo", repo, "--head", "engine/add-repo", "--state", "open", "--json", "number", "--jq", ".[0].number")] = (
        0,
        "42\n",
    )
    responses[("pr", "list", "--repo", engine_repo, "--head", "engine/add-my-repo", "--state", "open", "--json", "number", "--jq", ".[0].number")] = (
        0,
        "99\n",
    )
    gh = FakeGhRecorder(responses)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = False

    facts = gather_facts(repo, engine_repo, gh, probe)
    assert facts.adopt_pr_open is True
    assert facts.list_pr_open is True
    assert facts.pipeline_token_ok is False


# ===========================================================================
# 3. plan_add_repo
# ===========================================================================


def test_plan_blocked_when_pipeline_token_cannot_reach_repo() -> None:
    facts = _fully_wired_facts("owner/unreachable")
    facts = RepoFacts(**{**facts.__dict__, "pipeline_token_ok": False})
    plan = plan_add_repo(facts, no_jules=False)
    assert plan.blocked == (
        "PIPELINE_TOKEN cannot reach owner/unreachable: "
        "add the repo to that token at https://github.com/settings/personal-access-tokens"
    )
    assert plan.steps == []


def test_fully_wired_repo_plans_only_dry_run_and_jules_script() -> None:
    facts = _fully_wired_facts()
    plan = plan_add_repo(facts, no_jules=False)
    assert plan.blocked is None
    assert plan.steps == [StepKind.dry_run, StepKind.jules_script]


def test_plan_includes_missing_secrets_and_labels() -> None:
    facts = _fully_wired_facts()
    facts = RepoFacts(
        **{
            **facts.__dict__,
            "existing_secrets": ["PIPELINE_TOKEN"],  # missing JULES_API_KEY
            "existing_labels": ["engine:hold"],      # missing other required labels
        }
    )
    plan = plan_add_repo(facts, no_jules=False)
    assert SetSecretOp(name="JULES_API_KEY") in plan.steps
    assert any(isinstance(s, CreateLabelOp) for s in plan.steps)


def test_plan_includes_auto_merge_secret_scanning_push_protection() -> None:
    facts = _fully_wired_facts()
    facts = RepoFacts(
        **{
            **facts.__dict__,
            "allow_auto_merge": False,
            "secret_scanning_enabled": False,
            "push_protection_enabled": False,
        }
    )
    plan = plan_add_repo(facts, no_jules=False)
    assert EnableAutoMergeOp() in plan.steps
    assert EnableSecretScanningOp() in plan.steps
    assert EnablePushProtectionOp() in plan.steps


def test_plan_includes_actions_permissions_when_disabled_or_not_all() -> None:
    facts = _fully_wired_facts()
    facts_disabled = RepoFacts(**{**facts.__dict__, "actions_enabled": False})
    plan1 = plan_add_repo(facts_disabled, no_jules=False)
    assert ActionsPermissionsOp() in plan1.steps

    facts_restricted = RepoFacts(**{**facts.__dict__, "allowed_actions": "local_only"})
    plan2 = plan_add_repo(facts_restricted, no_jules=False)
    assert ActionsPermissionsOp() in plan2.steps


def test_plan_includes_adopt_pr_and_wait_adopt_when_no_config() -> None:
    facts = _fully_wired_facts()
    facts = RepoFacts(**{**facts.__dict__, "config_content": None, "adopt_pr_open": False})
    plan = plan_add_repo(facts, no_jules=False)
    assert StepKind.adopt_pr in plan.steps
    assert StepKind.wait_adopt in plan.steps
    assert StepKind.upgrade_pr not in plan.steps


def test_plan_omits_adopt_pr_when_adopt_pr_already_open() -> None:
    facts = _fully_wired_facts()
    facts = RepoFacts(**{**facts.__dict__, "config_content": None, "adopt_pr_open": True})
    plan = plan_add_repo(facts, no_jules=False)
    assert StepKind.adopt_pr not in plan.steps
    assert StepKind.wait_adopt in plan.steps


def test_plan_includes_upgrade_pr_when_config_needs_upgrade() -> None:
    facts = _fully_wired_facts()
    # Has box_enabled = true, needs upgrade
    old_config = 'default_branch = "master"\nbox_enabled = true\n'
    facts = RepoFacts(**{**facts.__dict__, "config_content": old_config, "adopt_pr_open": False})
    plan = plan_add_repo(facts, no_jules=False)
    assert StepKind.upgrade_pr in plan.steps
    assert StepKind.adopt_pr not in plan.steps
    assert StepKind.wait_adopt not in plan.steps


def test_plan_omits_upgrade_pr_when_adopt_pr_already_open() -> None:
    facts = _fully_wired_facts()
    old_config = 'default_branch = "master"\nbox_enabled = true\n'
    facts = RepoFacts(**{**facts.__dict__, "config_content": old_config, "adopt_pr_open": True})
    plan = plan_add_repo(facts, no_jules=False)
    assert StepKind.upgrade_pr not in plan.steps


def test_plan_includes_list_pr_when_not_in_engine_repos() -> None:
    facts = _fully_wired_facts()
    facts = RepoFacts(**{**facts.__dict__, "in_engine_repos": False, "list_pr_open": False})
    plan = plan_add_repo(facts, no_jules=False)
    assert StepKind.list_pr in plan.steps


def test_plan_omits_list_pr_when_list_pr_already_open() -> None:
    facts = _fully_wired_facts()
    facts = RepoFacts(**{**facts.__dict__, "in_engine_repos": False, "list_pr_open": True})
    plan = plan_add_repo(facts, no_jules=False)
    assert StepKind.list_pr not in plan.steps


def test_plan_includes_ruleset_when_missing() -> None:
    facts = _fully_wired_facts()
    facts = RepoFacts(**{**facts.__dict__, "existing_rulesets": []})
    plan = plan_add_repo(facts, no_jules=False)
    assert any(isinstance(s, CreateRulesetOp) for s in plan.steps)


def test_plan_jules_script_conditions() -> None:
    facts = _fully_wired_facts()
    # no_jules = True -> no jules_script
    plan_no_jules = plan_add_repo(facts, no_jules=True)
    assert StepKind.jules_script not in plan_no_jules.steps

    # config says jules_enabled = false -> no jules_script
    config_disabled = 'default_branch = "master"\njules_enabled = false\n'
    facts_disabled = RepoFacts(**{**facts.__dict__, "config_content": config_disabled})
    plan_disabled = plan_add_repo(facts_disabled, no_jules=False)
    assert StepKind.jules_script not in plan_disabled.steps


# ===========================================================================
# 4. config_upgrade
# ===========================================================================


def test_config_upgrade_removes_box_enabled() -> None:
    old = 'default_branch = "master"\nbox_enabled = true\npython_version = "3.12"\ninstall = "pip install -e .[dev]"\njules_enabled = true\n'
    upgraded = config_upgrade(old, no_jules=False)
    assert upgraded is not None
    assert "box_enabled" not in upgraded
    data = tomllib.loads(upgraded)
    assert data["default_branch"] == "master"
    assert data["python_version"] == "3.12"
    assert "box_enabled" not in data


def test_config_upgrade_appends_missing_keys() -> None:
    old = '# existing comment\ndefault_branch = "master"\n'
    upgraded = config_upgrade(old, no_jules=False)
    assert upgraded is not None
    assert "# existing comment" in upgraded
    data = tomllib.loads(upgraded)
    assert data["python_version"] == "3.12"
    assert data["install"] == "pip install -e .[dev]"
    assert data["jules_enabled"] is True


def test_config_upgrade_no_jules() -> None:
    old = 'default_branch = "master"\npython_version = "3.12"\ninstall = "pip install -e .[dev]"\njules_enabled = true\n'
    upgraded = config_upgrade(old, no_jules=True)
    assert upgraded is not None
    data = tomllib.loads(upgraded)
    assert data["jules_enabled"] is False


def test_config_upgrade_returns_none_when_no_change() -> None:
    current = 'default_branch = "master"\npython_version = "3.12"\ninstall = "pip install -e .[dev]"\njules_enabled = true\n'
    assert config_upgrade(current, no_jules=False) is None


# ===========================================================================
# 5. CLI --check output and privacy invariant
# ===========================================================================


def test_check_fully_wired_prints_nothing_to_do(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("PIPELINE_TOKEN=tok\n", encoding="utf-8")
    repo = "owner/wired"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    gh = FakeGhRecorder(responses)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    code = main([repo, "--check", "--secrets-file", str(secrets_file)], gh=gh, probe=probe)
    assert code == 0
    captured = capsys.readouterr()
    assert f"Nothing to do: {repo} is fully wired." in captured.out


def test_check_blocked_prints_blocked_and_returns_1(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("PIPELINE_TOKEN=tok\n", encoding="utf-8")
    repo = "owner/unreachable"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    gh = FakeGhRecorder(responses)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = False

    code = main([repo, "--check", "--secrets-file", str(secrets_file)], gh=gh, probe=probe)
    assert code == 1
    captured = capsys.readouterr()
    assert f"PIPELINE_TOKEN cannot reach {repo}" in captured.out


def test_check_runs_no_write_call_and_prints_no_secret_value(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secrets_file = tmp_path / "secrets.env"
    secret_val = "sekrit-value"
    secrets_file.write_text(f"PIPELINE_TOKEN={secret_val}\n", encoding="utf-8")
    repo = "owner/unwired"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    # Repo needs adopt and secrets
    responses[("api", f"repos/{repo}/contents/.ticket-engine.toml", "-H", "Accept: application/vnd.github.raw")] = (
        1,
        "404: Not Found",
    )
    responses[("api", f"repos/{repo}/actions/secrets", "--paginate", "--jq", ".secrets[].name")] = (
        0,
        "",
    )
    gh = FakeGhRecorder(responses)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    code = main([repo, "--check", "--secrets-file", str(secrets_file)], gh=gh, probe=probe)
    assert code == 0
    captured = capsys.readouterr()
    assert secret_val not in captured.out
    assert secret_val not in captured.err

    # Assert no write calls were made to gh
    for args, stdin in gh.calls:
        if args[0] == "api":
            assert "-X" not in args
            assert "--method" not in args
        else:
            assert args[:2] == ["pr", "list"]
        if stdin is not None:
            assert secret_val not in stdin


def test_without_check_prints_not_implemented_yet_and_returns_1(
    tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("PIPELINE_TOKEN=tok\n", encoding="utf-8")
    repo = "owner/wired"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    gh = FakeGhRecorder(responses)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    code = main([repo, "--secrets-file", str(secrets_file)], gh=gh, probe=probe)
    assert code == 1
    captured = capsys.readouterr()
    assert "not implemented yet" in captured.out
