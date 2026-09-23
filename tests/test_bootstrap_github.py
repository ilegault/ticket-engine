"""Tests for bootstrap GitHub side (ticket 11).

Covers acceptance criteria:
1. Reads secrets.env, schedules SetSecretOp for missing secrets; never echoes values;
   engine ships .env with key names only.
2. Schedules ops to enable auto-merge, secret scanning with push protection,
   and create a branch ruleset requiring the integrity check.
3. Schedules CreateLabelOp for engine:hold, engine:escalated, engine:windows-waiting.
4. Returns a Jules environment setup script and a manual-steps checklist.
5. All operations go through a fake-able adapter; second run schedules no ops.
"""
from __future__ import annotations

from ticket_engine.bootstrap import (
    ENGINE_RULESET_NAME,
    REQUIRED_SECRETS,
    CreateLabelOp,
    CreateRulesetOp,
    EnableAutoMergeOp,
    EnablePushProtectionOp,
    EnableSecretScanningOp,
    GitHubSetupInput,
    GitHubSetupResult,
    SetSecretOp,
    github_setup,
)

# ---------------------------------------------------------------------------
# Helpers / shared fixtures
# ---------------------------------------------------------------------------

def _base_input(**overrides) -> GitHubSetupInput:
    defaults: dict = {
        "repo": "owner/repo",
        "default_branch": "master",
        "existing_secret_names": [],
        "existing_label_names": [],
        "auto_merge_enabled": False,
        "secret_scanning_enabled": False,
        "push_protection_enabled": False,
        "existing_ruleset_names": [],
        "python_version": "3.12",
        "system_libraries": [],
    }
    defaults.update(overrides)
    return GitHubSetupInput(**defaults)


def _fully_configured_input() -> GitHubSetupInput:
    """Input representing a repo already fully configured by a prior run."""
    return _base_input(
        existing_secret_names=list(REQUIRED_SECRETS),
        existing_label_names=["engine:hold", "engine:escalated", "engine:windows-waiting"],
        auto_merge_enabled=True,
        secret_scanning_enabled=True,
        push_protection_enabled=True,
        existing_ruleset_names=[ENGINE_RULESET_NAME],
    )


def _ops_of(result: GitHubSetupResult, kind: type) -> list:
    return [op for op in result.operations if isinstance(op, kind)]


# ---------------------------------------------------------------------------
# Criterion 1: secrets
# ---------------------------------------------------------------------------

def test_all_three_secrets_scheduled_when_all_missing():
    result = github_setup(_base_input(existing_secret_names=[]))
    secret_ops = _ops_of(result, SetSecretOp)
    scheduled_names = {op.name for op in secret_ops}
    assert scheduled_names == set(REQUIRED_SECRETS)


def test_existing_secret_not_rescheduled():
    result = github_setup(_base_input(existing_secret_names=["JULES_API_KEY"]))
    secret_ops = _ops_of(result, SetSecretOp)
    scheduled_names = {op.name for op in secret_ops}
    assert "JULES_API_KEY" not in scheduled_names
    assert "PIPELINE_TOKEN" in scheduled_names
    assert "PEOPLE_DENYLIST" in scheduled_names


def test_all_secrets_present_zero_secret_ops():
    result = github_setup(_base_input(existing_secret_names=list(REQUIRED_SECRETS)))
    assert _ops_of(result, SetSecretOp) == []


def test_set_secret_op_carries_name_only_not_value():
    """SetSecretOp must carry only the key name; the adapter supplies the value."""
    result = github_setup(_base_input(existing_secret_names=[]))
    for op in _ops_of(result, SetSecretOp):
        assert hasattr(op, "name")
        assert not hasattr(op, "value")


def test_required_secrets_are_the_three_expected_keys():
    assert set(REQUIRED_SECRETS) == {"JULES_API_KEY", "PIPELINE_TOKEN", "PEOPLE_DENYLIST"}



# ---------------------------------------------------------------------------
# Criterion 2: repo settings (auto-merge, secret scanning, ruleset)
# ---------------------------------------------------------------------------

def test_auto_merge_op_scheduled_when_disabled():
    result = github_setup(_base_input(auto_merge_enabled=False))
    assert _ops_of(result, EnableAutoMergeOp), "Expected EnableAutoMergeOp when auto-merge is off"


def test_no_auto_merge_op_when_already_enabled():
    result = github_setup(_base_input(auto_merge_enabled=True))
    assert _ops_of(result, EnableAutoMergeOp) == []


def test_secret_scanning_op_scheduled_when_disabled():
    result = github_setup(_base_input(secret_scanning_enabled=False))
    assert _ops_of(result, EnableSecretScanningOp)


def test_no_secret_scanning_op_when_already_enabled():
    result = github_setup(_base_input(secret_scanning_enabled=True))
    assert _ops_of(result, EnableSecretScanningOp) == []


def test_push_protection_op_scheduled_when_disabled():
    result = github_setup(_base_input(push_protection_enabled=False))
    assert _ops_of(result, EnablePushProtectionOp)


def test_no_push_protection_op_when_already_enabled():
    result = github_setup(_base_input(push_protection_enabled=True))
    assert _ops_of(result, EnablePushProtectionOp) == []


def test_ruleset_op_scheduled_when_absent():
    result = github_setup(_base_input(existing_ruleset_names=[]))
    ruleset_ops = _ops_of(result, CreateRulesetOp)
    assert ruleset_ops, "Expected CreateRulesetOp when no ruleset exists"


def test_no_ruleset_op_when_already_present():
    result = github_setup(_base_input(existing_ruleset_names=[ENGINE_RULESET_NAME]))
    assert _ops_of(result, CreateRulesetOp) == []


def test_ruleset_op_targets_default_branch():
    result = github_setup(_base_input(default_branch="main", existing_ruleset_names=[]))
    ops = _ops_of(result, CreateRulesetOp)
    assert ops
    assert ops[0].default_branch == "main"


def test_ruleset_op_requires_integrity_check():
    result = github_setup(_base_input(existing_ruleset_names=[]))
    ops = _ops_of(result, CreateRulesetOp)
    assert ops
    checks = ops[0].required_checks
    assert any("integrity" in c for c in checks), (
        f"Ruleset required_checks must include integrity gate; got {checks!r}"
    )


def test_ruleset_op_uses_engine_ruleset_name():
    result = github_setup(_base_input(existing_ruleset_names=[]))
    ops = _ops_of(result, CreateRulesetOp)
    assert ops[0].name == ENGINE_RULESET_NAME


# ---------------------------------------------------------------------------
# Criterion 3: labels
# ---------------------------------------------------------------------------

def test_all_three_engine_labels_scheduled_when_absent():
    result = github_setup(_base_input(existing_label_names=[]))
    label_ops = _ops_of(result, CreateLabelOp)
    names = {op.name for op in label_ops}
    assert "engine:hold" in names
    assert "engine:escalated" in names
    assert "engine:windows-waiting" in names


def test_existing_label_not_rescheduled():
    result = github_setup(_base_input(existing_label_names=["engine:hold"]))
    label_ops = _ops_of(result, CreateLabelOp)
    names = {op.name for op in label_ops}
    assert "engine:hold" not in names
    assert "engine:escalated" in names
    assert "engine:windows-waiting" in names


def test_all_labels_present_zero_label_ops():
    result = github_setup(_base_input(
        existing_label_names=["engine:hold", "engine:escalated", "engine:windows-waiting"]
    ))
    assert _ops_of(result, CreateLabelOp) == []


def test_create_label_op_has_name_color_description():
    result = github_setup(_base_input(existing_label_names=[]))
    for op in _ops_of(result, CreateLabelOp):
        assert op.name
        assert op.color  # non-empty hex color
        assert op.description


# ---------------------------------------------------------------------------
# Criterion 4: Jules setup script and manual steps
# ---------------------------------------------------------------------------

def test_jules_setup_script_contains_python_version():
    result = github_setup(_base_input(python_version="3.11"))
    assert "3.11" in result.jules_setup_script


def test_jules_setup_script_contains_system_libraries():
    result = github_setup(_base_input(system_libraries=["libssl-dev", "libffi-dev"]))
    assert "libssl-dev" in result.jules_setup_script
    assert "libffi-dev" in result.jules_setup_script


def test_jules_setup_script_is_shell_script():
    result = github_setup(_base_input())
    script = result.jules_setup_script
    assert "bash" in script or "sh" in script or script.startswith("#")


def test_manual_steps_checklist_not_empty():
    result = github_setup(_base_input())
    assert len(result.manual_steps) >= 3, (
        f"Expected at least 3 manual steps; got {result.manual_steps!r}"
    )


def test_manual_steps_are_strings():
    result = github_setup(_base_input())
    for step in result.manual_steps:
        assert isinstance(step, str) and step.strip()


# ---------------------------------------------------------------------------
# Criterion 5: adapter pattern + idempotency
# ---------------------------------------------------------------------------

def test_second_run_no_operations_when_fully_configured():
    """A second run against an already-configured repo produces zero ops."""
    result = github_setup(_fully_configured_input())
    assert result.operations == [], (
        f"Second run produced unexpected operations: {result.operations!r}"
    )


def test_partial_first_run_only_missing_ops_scheduled():
    """If half the config is done, only the remaining ops are scheduled."""
    result = github_setup(_base_input(
        existing_secret_names=list(REQUIRED_SECRETS),
        auto_merge_enabled=True,
        secret_scanning_enabled=False,
        push_protection_enabled=True,
        existing_ruleset_names=[ENGINE_RULESET_NAME],
        existing_label_names=["engine:hold", "engine:escalated", "engine:windows-waiting"],
    ))
    op_types = {type(op) for op in result.operations}
    assert op_types == {EnableSecretScanningOp}


def test_operations_list_contains_only_known_op_types():
    result = github_setup(_base_input())
    known = (SetSecretOp, CreateLabelOp, EnableAutoMergeOp,
             EnableSecretScanningOp, EnablePushProtectionOp, CreateRulesetOp)
    for op in result.operations:
        assert isinstance(op, known), f"Unknown op type: {type(op)!r}"
