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
import shutil
import tomllib
from collections.abc import Callable
from unittest.mock import MagicMock

import pytest

from ticket_engine.add_repo import (
    ActionsPermissionsOp,
    RepoFacts,
    StepKind,
    apply_step,
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
from ticket_engine.repo_list import parse_repo_list


class FakeGhRecorder:
    def __init__(
        self,
        responses: dict[tuple[str, ...], tuple[int, str]] | None = None,
        clone_handler: Callable[[str, str], tuple[int, str]] | None = None,
    ) -> None:
        self.responses: dict[tuple[str, ...], tuple[int, str]] = dict(responses or {})
        self.calls: list[tuple[list[str], str | None]] = []
        self.clone_handler = clone_handler

    def __call__(self, args: list[str], stdin: str | None = None) -> tuple[int, str]:
        self.calls.append((list(args), stdin))
        if len(args) >= 4 and args[:2] == ["repo", "clone"] and self.clone_handler is not None:
            return self.clone_handler(args[2], args[3])
        key = tuple(args)
        if key in self.responses:
            return self.responses[key]
        return 0, ""


class FakeGitRecorder:
    def __init__(
        self,
        responses: dict[tuple[str, ...], tuple[int, str]] | None = None,
        callback: Callable[[list[str], str], None] | None = None,
    ) -> None:
        self.responses: dict[tuple[str, ...], tuple[int, str]] = dict(responses or {})
        self.calls: list[tuple[list[str], str]] = []
        self.callback = callback

    def __call__(self, args: list[str], cwd: str | pathlib.Path) -> tuple[int, str]:
        cwd_str = str(cwd)
        self.calls.append((list(args), cwd_str))
        if self.callback is not None:
            self.callback(list(args), cwd_str)
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


# ===========================================================================
# 6. Ticket 64: apply_step and settings operations
# ===========================================================================


def test_apply_step_set_secret() -> None:
    recorder = FakeGhRecorder()
    secrets = {"JULES_API_KEY": "secret-val-xyz"}
    apply_step(SetSecretOp("JULES_API_KEY"), "owner/target", recorder, secrets)
    assert len(recorder.calls) == 1
    args, stdin = recorder.calls[0]
    assert args == ["secret", "set", "JULES_API_KEY", "--repo", "owner/target"]
    assert stdin == "secret-val-xyz"


def test_apply_step_create_label() -> None:
    recorder = FakeGhRecorder()
    secrets = {}
    op = CreateLabelOp(name="engine:hold", color="d93f0b", description="held PR")
    apply_step(op, "owner/target", recorder, secrets)
    assert len(recorder.calls) == 1
    args, stdin = recorder.calls[0]
    assert args == [
        "label",
        "create",
        "engine:hold",
        "--repo",
        "owner/target",
        "--color",
        "d93f0b",
        "--description",
        "held PR",
    ]
    assert stdin is None


def test_apply_step_enable_auto_merge() -> None:
    recorder = FakeGhRecorder()
    secrets = {}
    apply_step(EnableAutoMergeOp(), "owner/target", recorder, secrets)
    assert len(recorder.calls) == 1
    args, stdin = recorder.calls[0]
    assert args == ["api", "-X", "PATCH", "repos/owner/target", "-F", "allow_auto_merge=true"]
    assert stdin is None


def test_apply_step_enable_secret_scanning() -> None:
    recorder = FakeGhRecorder()
    secrets = {}
    apply_step(EnableSecretScanningOp(), "owner/target", recorder, secrets)
    assert len(recorder.calls) == 1
    args, stdin = recorder.calls[0]
    assert args == ["api", "-X", "PATCH", "repos/owner/target", "--input", "-"]
    assert stdin == '{"security_and_analysis": {"secret_scanning": {"status": "enabled"}}}'


def test_apply_step_enable_push_protection() -> None:
    recorder = FakeGhRecorder()
    secrets = {}
    apply_step(EnablePushProtectionOp(), "owner/target", recorder, secrets)
    assert len(recorder.calls) == 1
    args, stdin = recorder.calls[0]
    assert args == ["api", "-X", "PATCH", "repos/owner/target", "--input", "-"]
    assert stdin == '{"security_and_analysis": {"secret_scanning_push_protection": {"status": "enabled"}}}'


def test_apply_step_actions_permissions() -> None:
    recorder = FakeGhRecorder()
    secrets = {}
    apply_step(ActionsPermissionsOp(), "owner/target", recorder, secrets)
    assert len(recorder.calls) == 1
    args, stdin = recorder.calls[0]
    assert args == [
        "api",
        "-X",
        "PUT",
        "repos/owner/target/actions/permissions",
        "-F",
        "enabled=true",
        "-f",
        "allowed_actions=all",
    ]
    assert stdin is None


def test_secret_value_only_on_stdin(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    secrets_file = tmp_path / "secrets.env"
    secret_val = "jk-value"
    secrets_file.write_text(
        f"PIPELINE_TOKEN=pt-val\nJULES_API_KEY={secret_val}\n",
        encoding="utf-8",
    )
    repo = "owner/unwired"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    responses[("api", f"repos/{repo}/actions/secrets", "--paginate", "--jq", ".secrets[].name")] = (
        0,
        "PIPELINE_TOKEN\n",
    )
    gh = FakeGhRecorder(responses)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    with caplog.at_level("DEBUG"):
        code = main([repo, "--secrets-file", str(secrets_file)], gh=gh, probe=probe)

    assert code == 1
    captured = capsys.readouterr()

    assert secret_val not in captured.out
    assert secret_val not in captured.err
    for record in caplog.records:
        assert secret_val not in record.getMessage()

    for args, _stdin in gh.calls:
        for arg in args:
            assert secret_val not in arg

    matching_calls = [
        (args, stdin)
        for args, stdin in gh.calls
        if args == ["secret", "set", "JULES_API_KEY", "--repo", repo]
    ]
    assert len(matching_calls) == 1
    assert matching_calls[0][1] == secret_val


def test_missing_secret_value_stops_before_any_write(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("PIPELINE_TOKEN=pt-val\n", encoding="utf-8")
    repo = "owner/unwired"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    responses[("api", f"repos/{repo}/actions/secrets", "--paginate", "--jq", ".secrets[].name")] = (
        0,
        "PIPELINE_TOKEN\n",
    )
    gh = FakeGhRecorder(responses)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    code = main([repo, "--secrets-file", str(secrets_file)], gh=gh, probe=probe)
    assert code == 2
    captured = capsys.readouterr()
    assert "secrets file has no JULES_API_KEY" in captured.out

    for args, _stdin in gh.calls:
        if args[0] == "api":
            assert "-X" not in args
            assert "--method" not in args
        elif args[0] == "secret":
            assert "set" not in args
        elif args[0] == "label":
            assert "create" not in args
        else:
            assert args[:2] == ["pr", "list"]


def test_failed_step_stops_and_names_it(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text(
        "PIPELINE_TOKEN=pt-val\nJULES_API_KEY=jk-val\n",
        encoding="utf-8",
    )
    repo = "owner/unwired"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    responses[("api", f"repos/{repo}")] = (
        0,
        (
            '{"default_branch": "master", "allow_auto_merge": false, '
            '"security_and_analysis": {"secret_scanning": {"status": "enabled"}, '
            '"secret_scanning_push_protection": {"status": "enabled"}}}'
        ),
    )
    responses[("api", f"repos/{repo}/labels", "--paginate", "--jq", ".[].name")] = (
        0,
        "",
    )
    responses[("api", "-X", "PATCH", f"repos/{repo}", "-F", "allow_auto_merge=true")] = (
        1,
        "error: unable to enable auto merge\nHTTP 403: Forbidden",
    )
    gh = FakeGhRecorder(responses)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    code = main([repo, "--secrets-file", str(secrets_file)], gh=gh, probe=probe)
    assert code == 1
    captured = capsys.readouterr()
    assert "step failed: enable_auto_merge: HTTP 403: Forbidden" in captured.out

    label_calls = [args for args, _stdin in gh.calls if args[:2] == ["label", "create"]]
    assert len(label_calls) == 0


def test_rerun_after_success_plans_no_settings_steps(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text(
        "PIPELINE_TOKEN=pt-val\nJULES_API_KEY=jk-val\n",
        encoding="utf-8",
    )
    repo = "owner/wired"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    gh = FakeGhRecorder(responses)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    code = main([repo, "--secrets-file", str(secrets_file)], gh=gh, probe=probe)
    assert code == 1
    captured = capsys.readouterr()
    assert "not implemented yet: dry_run, jules_script" in captured.out

    for args, _stdin in gh.calls:
        if args[0] == "api":
            assert "-X" not in args
            assert "--method" not in args
        elif args[0] in ("secret", "label"):
            pytest.fail(f"Unexpected settings write call: {args}")


# ===========================================================================
# 7. Ticket 65: adopt PR, upgrade PR, repo-list PR
# ===========================================================================


def test_adopt_pr_commands_in_order(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture_dir = tmp_path / "fixture_repo"
    fixture_dir.mkdir()
    (fixture_dir / "AGENTS.md").write_text("# Test Repo\n", encoding="utf-8")
    issues_dir = fixture_dir / ".scratch" / "test-effort" / "issues"
    issues_dir.mkdir(parents=True)
    (issues_dir / "01-test.md").write_text("# 01: Test\nStatus: ready-for-agent\n", encoding="utf-8")

    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("PIPELINE_TOKEN=pt-val\nJULES_API_KEY=jk-val\n", encoding="utf-8")
    repo = "owner/target"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    # Repo has no config -> plans adopt_pr
    responses[("api", f"repos/{repo}/contents/.ticket-engine.toml", "-H", "Accept: application/vnd.github.raw")] = (
        1,
        "404: Not Found",
    )

    cloned_dest: list[str] = []

    def clone_handler(repo_arg: str, target_dir: str) -> tuple[int, str]:
        cloned_dest.append(target_dir)
        shutil.copytree(fixture_dir, target_dir)
        return 0, ""

    gh = FakeGhRecorder(responses, clone_handler=clone_handler)
    git = FakeGitRecorder()
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    code = main([repo, "--secrets-file", str(secrets_file)], gh=gh, git=git, probe=probe)
    assert code == 1
    captured = capsys.readouterr()
    assert "not implemented yet: wait_adopt, dry_run, jules_script" in captured.out

    assert len(cloned_dest) == 1
    clone_path = cloned_dest[0]

    # Verify exact git calls in order
    assert len(git.calls) == 4
    assert git.calls[0] == (["checkout", "-b", "engine/add-repo"], clone_path)
    assert git.calls[1] == (["add", "-A"], clone_path)
    assert git.calls[2] == (["commit", "-m", f"Wire {repo} to ticket-engine (add-repo)"], clone_path)
    assert git.calls[3] == (["push", "-u", "origin", "engine/add-repo"], clone_path)

    # Verify gh calls: clone was first write call, pr create was after push
    write_gh_calls = [
        args for args, _stdin in gh.calls if args[0] != "api" and args[:2] != ["pr", "list"]
    ]
    assert write_gh_calls == [
        ["repo", "clone", repo, clone_path],
        [
            "pr",
            "create",
            "--repo",
            repo,
            "--base",
            "master",
            "--head",
            "engine/add-repo",
            "--title",
            "Wire to ticket-engine",
            "--body",
            "Opened by add-repo (ADR 0009). Merge by hand.",
        ],
    ]


def test_pii_finding_stops_before_commit_and_prints_paths_only(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture_dir = tmp_path / "fixture_repo"
    fixture_dir.mkdir()
    (fixture_dir / "AGENTS.md").write_text("# Test Repo\n", encoding="utf-8")
    src_dir = fixture_dir / "src"
    src_dir.mkdir()
    pii_file = src_dir / "contact.py"
    pii_file.write_text("developer_email = 'realperson@example.com'\n", encoding="utf-8")

    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("PIPELINE_TOKEN=pt-val\nJULES_API_KEY=jk-val\n", encoding="utf-8")
    repo = "owner/target"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    responses[("api", f"repos/{repo}/contents/.ticket-engine.toml", "-H", "Accept: application/vnd.github.raw")] = (
        1,
        "404: Not Found",
    )

    def clone_handler(repo_arg: str, target_dir: str) -> tuple[int, str]:
        shutil.copytree(fixture_dir, target_dir)
        return 0, ""

    gh = FakeGhRecorder(responses, clone_handler=clone_handler)
    git = FakeGitRecorder()
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    code = main([repo, "--secrets-file", str(secrets_file)], gh=gh, git=git, probe=probe)
    assert code == 1
    captured = capsys.readouterr()

    # Prints file path only
    assert "src/contact.py" in captured.out
    assert "realperson@example.com" not in captured.out
    assert "realperson@example.com" not in captured.err
    assert "email" not in captured.out
    assert "email" not in captured.err

    # Git: checkout was called, but NO commit or push
    git_commands = [args[0] for args, _cwd in git.calls]
    assert "checkout" in git_commands
    assert "commit" not in git_commands
    assert "push" not in git_commands

    # gh: clone was called, but NO pr create
    gh_pr_creates = [args for args, _stdin in gh.calls if args[:2] == ["pr", "create"]]
    assert len(gh_pr_creates) == 0


def test_upgrade_pr_changes_only_the_engine_config(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old_config = 'default_branch = "master"\nbox_enabled = true\n'
    fixture_dir = tmp_path / "fixture_repo"
    fixture_dir.mkdir()
    (fixture_dir / ".ticket-engine.toml").write_text(old_config, encoding="utf-8")
    (fixture_dir / "existing.txt").write_text("keep\n", encoding="utf-8")

    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("PIPELINE_TOKEN=pt-val\nJULES_API_KEY=jk-val\n", encoding="utf-8")
    repo = "owner/target"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    responses[("api", f"repos/{repo}/contents/.ticket-engine.toml", "-H", "Accept: application/vnd.github.raw")] = (
        0,
        old_config,
    )

    cloned_dest: list[str] = []
    config_at_commit: list[str] = []

    def clone_handler(repo_arg: str, target_dir: str) -> tuple[int, str]:
        cloned_dest.append(target_dir)
        shutil.copytree(fixture_dir, target_dir)
        return 0, ""

    def git_callback(args: list[str], cwd: str) -> None:
        if args[:2] == ["commit", "-m"]:
            cfg_file = pathlib.Path(cwd) / ".ticket-engine.toml"
            if cfg_file.is_file():
                config_at_commit.append(cfg_file.read_text(encoding="utf-8"))

    gh = FakeGhRecorder(responses, clone_handler=clone_handler)
    git = FakeGitRecorder(callback=git_callback)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    code = main([repo, "--secrets-file", str(secrets_file)], gh=gh, git=git, probe=probe)
    assert code == 1

    assert len(cloned_dest) == 1
    clone_path = cloned_dest[0]

    # Verify git add names ONLY .ticket-engine.toml
    git_add_calls = [args for args, _cwd in git.calls if args[0] == "add"]
    assert len(git_add_calls) == 1
    assert git_add_calls[0] == ["add", ".ticket-engine.toml"]

    # Verify commit message
    git_commit_calls = [args for args, _cwd in git.calls if args[0] == "commit"]
    assert len(git_commit_calls) == 1
    assert git_commit_calls[0] == ["commit", "-m", "Upgrade .ticket-engine.toml for add-repo (ADR 0009/0010)"]

    # Verify PR title
    gh_pr_creates = [args for args, _stdin in gh.calls if args[:2] == ["pr", "create"]]
    assert len(gh_pr_creates) == 1
    assert "--title" in gh_pr_creates[0]
    title_idx = gh_pr_creates[0].index("--title")
    assert gh_pr_creates[0][title_idx + 1] == "Upgrade ticket-engine config"

    # Verify file at commit time has no box_enabled and parses
    assert len(config_at_commit) == 1
    text = config_at_commit[0]
    assert "box_enabled" not in text
    data = tomllib.loads(text)
    assert data["default_branch"] == "master"
    assert data["python_version"] == "3.12"

    # Verify run_adopt was NOT called
    assert not (pathlib.Path(clone_path) / ".github").exists()


def test_list_pr_appends_one_parsable_entry(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    initial_engine_repos = '[[repos]]\nrepo = "owner/existing"\n'
    fixture_engine = tmp_path / "fixture_engine"
    fixture_engine.mkdir()
    (fixture_engine / "engine-repos.toml").write_text(initial_engine_repos, encoding="utf-8")

    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("PIPELINE_TOKEN=pt-val\nJULES_API_KEY=jk-val\n", encoding="utf-8")
    repo = "owner/new-repo"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    responses[("api", f"repos/{engine_repo}/contents/engine-repos.toml", "-H", "Accept: application/vnd.github.raw")] = (
        0,
        initial_engine_repos,
    )

    cloned_dest: list[str] = []
    engine_repos_at_commit: list[str] = []

    def clone_handler(repo_arg: str, target_dir: str) -> tuple[int, str]:
        cloned_dest.append(target_dir)
        shutil.copytree(fixture_engine, target_dir)
        return 0, ""

    def git_callback(args: list[str], cwd: str) -> None:
        if args[:2] == ["commit", "-m"]:
            f = pathlib.Path(cwd) / "engine-repos.toml"
            if f.is_file():
                engine_repos_at_commit.append(f.read_text(encoding="utf-8"))

    gh = FakeGhRecorder(responses, clone_handler=clone_handler)
    git = FakeGitRecorder(callback=git_callback)
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    # 1. Test with default (box = true, no --no-box flag)
    code = main([repo, "--secrets-file", str(secrets_file)], gh=gh, git=git, probe=probe)
    assert code == 1

    assert len(cloned_dest) == 1
    clone_path = cloned_dest[0]

    assert git.calls[0] == (["checkout", "-b", "engine/add-new-repo"], clone_path)
    assert git.calls[1] == (["add", "engine-repos.toml"], clone_path)
    assert git.calls[2] == (["commit", "-m", f"Add {repo} to the repo list"], clone_path)
    assert git.calls[3] == (["push", "-u", "origin", "engine/add-new-repo"], clone_path)

    gh_pr_creates = [args for args, _stdin in gh.calls if args[:2] == ["pr", "create"]]
    assert len(gh_pr_creates) == 1
    assert gh_pr_creates[0] == [
        "pr",
        "create",
        "--repo",
        engine_repo,
        "--base",
        "master",
        "--head",
        "engine/add-new-repo",
        "--title",
        f"Add {repo} to the repo list",
        "--body",
        "Opened by add-repo (ADR 0009). Merging this turns the repo on.",
    ]

    assert len(engine_repos_at_commit) == 1
    entries = parse_repo_list(engine_repos_at_commit[0])
    assert any(e.repo == repo and e.box is True for e in entries)

    # 2. Test with --no-box flag
    cloned_dest.clear()
    engine_repos_at_commit.clear()
    gh = FakeGhRecorder(responses, clone_handler=clone_handler)
    git = FakeGitRecorder(callback=git_callback)

    code2 = main([repo, "--no-box", "--secrets-file", str(secrets_file)], gh=gh, git=git, probe=probe)
    assert code2 == 1
    assert len(engine_repos_at_commit) == 1
    entries2 = parse_repo_list(engine_repos_at_commit[0])
    assert any(e.repo == repo and e.box is False for e in entries2)


def test_failed_push_stops_and_cleans_up(
    tmp_path: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture_dir = tmp_path / "fixture_repo"
    fixture_dir.mkdir()
    (fixture_dir / "AGENTS.md").write_text("# Test Repo\n", encoding="utf-8")

    secrets_file = tmp_path / "secrets.env"
    secrets_file.write_text("PIPELINE_TOKEN=pt-val\nJULES_API_KEY=jk-val\n", encoding="utf-8")
    repo = "owner/target"
    engine_repo = "ilegault/ticket-engine"
    responses = _default_fully_wired_responses(repo, engine_repo)
    responses[("api", f"repos/{repo}/contents/.ticket-engine.toml", "-H", "Accept: application/vnd.github.raw")] = (
        1,
        "404: Not Found",
    )

    cloned_paths: list[pathlib.Path] = []

    def clone_handler(repo_arg: str, target_dir: str) -> tuple[int, str]:
        p = pathlib.Path(target_dir)
        cloned_paths.append(p)
        shutil.copytree(fixture_dir, target_dir)
        return 0, ""

    gh = FakeGhRecorder(responses, clone_handler=clone_handler)
    git = FakeGitRecorder(
        responses={
            ("push", "-u", "origin", "engine/add-repo"): (
                1,
                "error: failed to push some refs\nremote: rejected",
            ),
        }
    )
    probe = MagicMock(spec=GitHubClient)
    probe.can_read_variables.return_value = True

    code = main([repo, "--secrets-file", str(secrets_file)], gh=gh, git=git, probe=probe)
    assert code == 1
    captured = capsys.readouterr()
    assert "step failed: adopt_pr: remote: rejected" in captured.out

    # Verify temp dir cleanup
    assert len(cloned_paths) == 1
    assert not cloned_paths[0].exists()
    assert not cloned_paths[0].parent.exists()

    # Verify no PR was created
    gh_pr_creates = [args for args, _stdin in gh.calls if args[:2] == ["pr", "create"]]
    assert len(gh_pr_creates) == 0


