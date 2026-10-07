"""The add-repo command: reads a repo's state, plans the wiring, and carries it out.

WHY THIS EXISTS
---------------
ADR 0009 rule 1: `add-repo owner/name` is an engine console command run on the
developer's PC. It acts through the developer's own `gh` login (repo admin), so it
needs no new token. It is idempotent: it applies only what is missing, and
`--check` prints what it would do and changes nothing. `--no-jules` marks a repo
whose suite cannot pass on Linux.

ADR 0009 rule 2: Applies settings, labels, secrets from secrets.env, and permission
to use the engine's workflows (ticket 64).

ADR 0002 rule 3: Secrets live in one `secrets.env` outside every repo on the
developer's machine. Secret values are never printed, logged, or passed in
command arguments; they reach `gh` only on stdin.

AGENTS.md §3: Three pure cores; decisions live in pure functions (`plan_add_repo`,
`config_upgrade`). Adapters gather facts and run commands.
"""
from __future__ import annotations

import argparse
import json
import logging
import pathlib
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum

from ticket_engine.bootstrap import (
    _INTEGRITY_CHECK_CONTEXT,
    ENGINE_RULESET_NAME,
    CreateLabelOp,
    CreateRulesetOp,
    EnableAutoMergeOp,
    EnablePushProtectionOp,
    EnableSecretScanningOp,
    GitHubSetupInput,
    SetSecretOp,
    github_setup,
)
from ticket_engine.github import GitHubClient
from ticket_engine.repo_list import parse_repo_list

logger = logging.getLogger(__name__)

GhRunner = Callable[[list[str], str | None], tuple[int, str]]


def default_gh_runner(args: list[str], stdin: str | None = None) -> tuple[int, str]:
    """Execute a gh CLI command and return (returncode, output)."""
    res = subprocess.run(
        ["gh", *args],
        input=stdin,
        capture_output=True,
        text=True,
        check=False,
    )
    output = res.stdout if res.returncode == 0 else (res.stderr or res.stdout)
    return res.returncode, output


def load_secrets_file(path: pathlib.Path | str) -> dict[str, str]:
    """Read KEY=VALUE lines, ignoring blank lines and # comments, stripping surrounding quotes."""
    p = pathlib.Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"Secrets file not found: {p}")

    secrets: dict[str, str] = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, val = line.split("=", 1)
        key = key.strip()
        val = val.strip()
        if len(val) >= 2 and ((val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'"))):
            val = val[1:-1]
        secrets[key] = val

    return secrets


@dataclass
class RepoFacts:
    """Facts gathered from GitHub about the target repo and engine repo."""

    repo: str
    default_branch: str
    allow_auto_merge: bool
    secret_scanning_enabled: bool
    push_protection_enabled: bool
    existing_secrets: list[str]
    existing_labels: list[str]
    existing_rulesets: list[str]
    actions_enabled: bool
    allowed_actions: str
    config_content: str | None
    adopt_pr_open: bool
    in_engine_repos: bool
    list_pr_open: bool
    pipeline_token_ok: bool
    adopt_pr_number: int | None = None
    list_pr_number: int | None = None


def gather_facts(
    repo: str,
    engine_repo: str,
    gh: GhRunner,
    probe: GitHubClient,
) -> RepoFacts:
    """Gather current repo state using read-only gh calls and the PIPELINE_TOKEN probe."""
    repo = repo.strip()
    engine_repo = engine_repo.strip()
    repo_name = repo.split("/")[-1]

    # 1. Repo info: default_branch, allow_auto_merge, secret scanning
    _code, out = gh(["api", f"repos/{repo}"])
    repo_data = json.loads(out)
    default_branch = repo_data.get("default_branch", "master")
    allow_auto_merge = bool(repo_data.get("allow_auto_merge", False))
    sec_analysis = repo_data.get("security_and_analysis") or {}
    secret_scanning_status = (sec_analysis.get("secret_scanning") or {}).get("status", "")
    push_protection_status = (sec_analysis.get("secret_scanning_push_protection") or {}).get("status", "")
    secret_scanning_enabled = secret_scanning_status == "enabled"
    push_protection_enabled = push_protection_status == "enabled"

    # 2. Existing secrets
    _code, out = gh(["api", f"repos/{repo}/actions/secrets", "--paginate", "--jq", ".secrets[].name"])
    existing_secrets = [line.strip() for line in out.splitlines() if line.strip()]

    # 3. Existing labels
    _code, out = gh(["api", f"repos/{repo}/labels", "--paginate", "--jq", ".[].name"])
    existing_labels = [line.strip() for line in out.splitlines() if line.strip()]

    # 4. Existing rulesets
    _code, out = gh(["api", f"repos/{repo}/rulesets", "--jq", ".[].name"])
    existing_rulesets = [line.strip() for line in out.splitlines() if line.strip()]

    # 5. Actions permissions
    _code, out = gh(["api", f"repos/{repo}/actions/permissions"])
    perm_data = json.loads(out)
    actions_enabled = bool(perm_data.get("enabled", False))
    allowed_actions = str(perm_data.get("allowed_actions", ""))

    # 6. Config content
    code, out = gh(["api", f"repos/{repo}/contents/.ticket-engine.toml", "-H", "Accept: application/vnd.github.raw"])
    if code != 0 and "404" in out:
        config_content = None
    else:
        config_content = out

    # 7. Open adopt PR
    _code, out = gh(
        ["pr", "list", "--repo", repo, "--head", "engine/add-repo", "--state", "open", "--json", "number", "--jq", ".[0].number"]
    )
    adopt_str = out.strip()
    adopt_pr_open = bool(adopt_str and adopt_str != "null")
    adopt_pr_number = int(adopt_str) if adopt_pr_open and adopt_str.isdigit() else None

    # 8. engine-repos.toml
    _code, out = gh(["api", f"repos/{engine_repo}/contents/engine-repos.toml", "-H", "Accept: application/vnd.github.raw"])
    engine_repos_entries = parse_repo_list(out)
    in_engine_repos = any(e.repo == repo for e in engine_repos_entries)

    # 9. Open list PR
    _code, out = gh(
        [
            "pr",
            "list",
            "--repo",
            engine_repo,
            "--head",
            f"engine/add-{repo_name}",
            "--state",
            "open",
            "--json",
            "number",
            "--jq",
            ".[0].number",
        ]
    )
    list_str = out.strip()
    list_pr_open = bool(list_str and list_str != "null")
    list_pr_number = int(list_str) if list_pr_open and list_str.isdigit() else None

    # Token probe
    pipeline_token_ok = probe.can_read_variables(repo)

    return RepoFacts(
        repo=repo,
        default_branch=default_branch,
        allow_auto_merge=allow_auto_merge,
        secret_scanning_enabled=secret_scanning_enabled,
        push_protection_enabled=push_protection_enabled,
        existing_secrets=existing_secrets,
        existing_labels=existing_labels,
        existing_rulesets=existing_rulesets,
        actions_enabled=actions_enabled,
        allowed_actions=allowed_actions,
        config_content=config_content,
        adopt_pr_open=adopt_pr_open,
        in_engine_repos=in_engine_repos,
        list_pr_open=list_pr_open,
        pipeline_token_ok=pipeline_token_ok,
        adopt_pr_number=adopt_pr_number,
        list_pr_number=list_pr_number,
    )


@dataclass(frozen=True)
class ActionsPermissionsOp:
    pass


class StepKind(str, Enum):
    adopt_pr = "adopt_pr"
    upgrade_pr = "upgrade_pr"
    list_pr = "list_pr"
    wait_adopt = "wait_adopt"
    dry_run = "dry_run"
    jules_script = "jules_script"


Step = (
    SetSecretOp
    | CreateLabelOp
    | EnableAutoMergeOp
    | EnableSecretScanningOp
    | EnablePushProtectionOp
    | ActionsPermissionsOp
    | CreateRulesetOp
    | StepKind
)


@dataclass(frozen=True)
class AddRepoPlan:
    blocked: str | None
    steps: list[Step]


def config_upgrade(text: str, no_jules: bool) -> str | None:
    """Pure text edit upgrading .ticket-engine.toml. Returns None if no changes needed."""
    try:
        parsed = tomllib.loads(text)
    except Exception as exc:
        raise ValueError(f"Invalid TOML: {exc}") from exc

    lines = text.splitlines(keepends=True)
    new_lines: list[str] = []
    has_jules_enabled_line = False

    for line in lines:
        if re.match(r"^\s*box_enabled\s*=", line):
            continue
        if re.match(r"^\s*jules_enabled\s*=", line):
            has_jules_enabled_line = True
            if no_jules:
                nl = "\r\n" if line.endswith("\r\n") else ("\n" if line.endswith("\n") else "")
                indent = re.match(r"^(\s*)", line).group(1)
                new_lines.append(f"{indent}jules_enabled = false{nl}")
            else:
                new_lines.append(line)
            continue
        new_lines.append(line)

    lines_to_append: list[str] = []
    if "python_version" not in parsed:
        lines_to_append.append('python_version = "3.12"')
    if "install" not in parsed:
        lines_to_append.append('install = "pip install -e .[dev]"')
    if "jules_enabled" not in parsed and not has_jules_enabled_line:
        lines_to_append.append("jules_enabled = false" if no_jules else "jules_enabled = true")

    result_text = "".join(new_lines)
    if lines_to_append:
        if result_text and not result_text.endswith("\n"):
            result_text += "\n"
        result_text += "\n".join(lines_to_append) + "\n"

    if result_text == text:
        return None

    tomllib.loads(result_text)
    return result_text


def plan_add_repo(facts: RepoFacts, no_jules: bool) -> AddRepoPlan:
    """Compute the AddRepoPlan from facts. Pure function."""
    if not facts.pipeline_token_ok:
        blocked = (
            f"PIPELINE_TOKEN cannot reach {facts.repo}: "
            f"add the repo to that token at https://github.com/settings/personal-access-tokens"
        )
        return AddRepoPlan(blocked=blocked, steps=[])

    steps: list[Step] = []

    # Config options
    python_version = "3.12"
    system_libraries: list[str] = []
    install = "pip install -e .[dev]"
    config_jules = True

    if facts.config_content is not None:
        try:
            cfg = tomllib.loads(facts.config_content)
            python_version = cfg.get("python_version", "3.12")
            system_libraries = cfg.get("system_libraries", [])
            install = cfg.get("install", "pip install -e .[dev]")
            config_jules = cfg.get("jules_enabled", True)
        except tomllib.TOMLDecodeError:
            logger.warning("Failed to parse config_content in plan_add_repo")

    # 1. github_setup ops except CreateRulesetOp
    setup_input = GitHubSetupInput(
        repo=facts.repo,
        default_branch=facts.default_branch,
        existing_secret_names=facts.existing_secrets,
        existing_label_names=facts.existing_labels,
        auto_merge_enabled=facts.allow_auto_merge,
        secret_scanning_enabled=facts.secret_scanning_enabled,
        push_protection_enabled=facts.push_protection_enabled,
        existing_ruleset_names=facts.existing_rulesets,
        python_version=python_version,
        system_libraries=system_libraries,
        install=install,
    )
    setup_res = github_setup(setup_input)
    for op in setup_res.operations:
        if not isinstance(op, CreateRulesetOp):
            steps.append(op)

    # 2. Actions permissions
    if not facts.actions_enabled or facts.allowed_actions != "all":
        steps.append(ActionsPermissionsOp())

    # 3. adopt_pr or upgrade_pr
    if facts.config_content is None:
        if not facts.adopt_pr_open:
            steps.append(StepKind.adopt_pr)
    else:
        if not facts.adopt_pr_open and config_upgrade(facts.config_content, no_jules) is not None:
            steps.append(StepKind.upgrade_pr)

    # 4. list_pr
    if not facts.in_engine_repos and not facts.list_pr_open:
        steps.append(StepKind.list_pr)

    # 5. wait_adopt
    if facts.config_content is None:
        steps.append(StepKind.wait_adopt)

    # 6. CreateRulesetOp
    if ENGINE_RULESET_NAME not in facts.existing_rulesets:
        steps.append(
            CreateRulesetOp(
                name=ENGINE_RULESET_NAME,
                default_branch=facts.default_branch,
                required_checks=(_INTEGRITY_CHECK_CONTEXT,),
            )
        )

    # 7. dry_run
    steps.append(StepKind.dry_run)

    # 8. jules_script
    if not no_jules and config_jules is not False:
        steps.append(StepKind.jules_script)

    return AddRepoPlan(blocked=None, steps=steps)


def step_kind(step: Step) -> str:
    """Return the step kind string name."""
    if isinstance(step, StepKind):
        return step.value
    if isinstance(step, SetSecretOp):
        return "set_secret"
    if isinstance(step, CreateLabelOp):
        return "create_label"
    if isinstance(step, EnableAutoMergeOp):
        return "enable_auto_merge"
    if isinstance(step, EnableSecretScanningOp):
        return "enable_secret_scanning"
    if isinstance(step, EnablePushProtectionOp):
        return "enable_push_protection"
    if isinstance(step, ActionsPermissionsOp):
        return "actions_permissions"
    if isinstance(step, CreateRulesetOp):
        return "create_ruleset"
    return str(step)


def format_step(step: Step) -> str:
    """Format step for CLI printing."""
    kind = step_kind(step)
    if isinstance(step, (SetSecretOp, CreateLabelOp, CreateRulesetOp)):
        return f"{kind}: {step.name}"
    return kind


SETTINGS_OPS = (
    SetSecretOp,
    CreateLabelOp,
    EnableAutoMergeOp,
    EnableSecretScanningOp,
    EnablePushProtectionOp,
    ActionsPermissionsOp,
)


class StepFailedError(RuntimeError):
    """Raised when a gh call in apply_step fails."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(f"step failed: {kind}: {message}")
        self.kind = kind
        self.message = message


StepFailed = StepFailedError


def apply_step(
    step: Step,
    repo: str,
    gh: GhRunner,
    secrets: dict[str, str],
) -> None:
    """Apply a settings step using an exact gh CLI call.

    Raises StepFailedError if gh returns a non-zero exit code.
    """
    args: list[str]
    stdin: str | None = None

    if isinstance(step, SetSecretOp):
        args = ["secret", "set", step.name, "--repo", repo]
        stdin = secrets[step.name]
    elif isinstance(step, CreateLabelOp):
        args = [
            "label",
            "create",
            step.name,
            "--repo",
            repo,
            "--color",
            step.color,
            "--description",
            step.description,
        ]
    elif isinstance(step, EnableAutoMergeOp):
        args = ["api", "-X", "PATCH", f"repos/{repo}", "-F", "allow_auto_merge=true"]
    elif isinstance(step, EnableSecretScanningOp):
        args = ["api", "-X", "PATCH", f"repos/{repo}", "--input", "-"]
        stdin = '{"security_and_analysis": {"secret_scanning": {"status": "enabled"}}}'
    elif isinstance(step, EnablePushProtectionOp):
        args = ["api", "-X", "PATCH", f"repos/{repo}", "--input", "-"]
        stdin = '{"security_and_analysis": {"secret_scanning_push_protection": {"status": "enabled"}}}'
    elif isinstance(step, ActionsPermissionsOp):
        args = [
            "api",
            "-X",
            "PUT",
            f"repos/{repo}/actions/permissions",
            "-F",
            "enabled=true",
            "-f",
            "allowed_actions=all",
        ]
    else:
        raise TypeError(f"apply_step called with non-settings step: {step}")

    code, out = gh(args, stdin=stdin)
    if code != 0:
        lines = [line.strip() for line in out.strip().splitlines() if line.strip()]
        last_line = lines[-1] if lines else ""
        raise StepFailedError(step_kind(step), last_line)


def main(
    argv: list[str] | None = None,
    *,
    gh: GhRunner | None = None,
    probe: GitHubClient | None = None,
) -> int:
    """CLI entry point for add-repo."""
    parser = argparse.ArgumentParser(
        prog="add-repo",
        description="Add a repository to ticket-engine or check its wiring state.",
    )
    parser.add_argument("repo", help="Target repo (owner/name)")
    parser.add_argument("--check", action="store_true", help="Print the plan and change nothing")
    parser.add_argument("--no-jules", action="store_true", help="Mark repo as not supported on Jules Linux")
    parser.add_argument(
        "--secrets-file",
        default=str(pathlib.Path.home() / "secrets.env"),
        help="Path to secrets.env file",
    )
    parser.add_argument(
        "--engine-repo",
        default="ilegault/ticket-engine",
        help="Engine repo identifier (default: ilegault/ticket-engine)",
    )

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    secrets_path = pathlib.Path(args.secrets_file)
    try:
        secrets = load_secrets_file(secrets_path)
    except FileNotFoundError:
        print(f"Secrets file not found: {secrets_path}")
        return 2

    if "PIPELINE_TOKEN" not in secrets or not secrets["PIPELINE_TOKEN"]:
        print(f"PIPELINE_TOKEN missing from secrets file: {secrets_path}")
        return 2

    runner = gh if gh is not None else default_gh_runner
    client_probe = probe if probe is not None else GitHubClient(token=secrets["PIPELINE_TOKEN"])

    facts = gather_facts(args.repo, args.engine_repo, runner, client_probe)
    plan = plan_add_repo(facts, no_jules=args.no_jules)

    if args.check:
        if plan.blocked is not None:
            print(plan.blocked)
            return 1

        if all(isinstance(s, StepKind) and s in (StepKind.dry_run, StepKind.jules_script) for s in plan.steps):
            print(f"Nothing to do: {args.repo} is fully wired.")
            return 0

        for s in plan.steps:
            print(format_step(s))
        return 0

    if plan.blocked is not None:
        print(plan.blocked)
        return 1

    # Check that all secret values exist before making any write calls
    for step in plan.steps:
        if isinstance(step, SetSecretOp) and step.name not in secrets:
            print(f"secrets file has no {step.name}")
            return 2

    # Carry out settings steps
    for step in plan.steps:
        if isinstance(step, SETTINGS_OPS):
            try:
                apply_step(step, args.repo, runner, secrets)
            except StepFailedError as exc:
                print(f"step failed: {exc.kind}: {exc.message}")
                return 1

    # Remaining steps not yet implemented (tickets 65-66)
    remaining_steps = [s for s in plan.steps if not isinstance(s, SETTINGS_OPS)]
    if remaining_steps:
        kinds = ", ".join(step_kind(s) for s in remaining_steps)
        print(f"not implemented yet: {kinds}")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
