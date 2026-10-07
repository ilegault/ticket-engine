# 63: `add-repo --check` reads a repo's state and prints the plan

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 53, 54

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0009 rules 1, 2, 3 and 6; ADR 0002 rule 3 (secret values never printed); AGENTS.md §3 (decisions in pure code)

## What to build

The first slice of the `add-repo` command, run on the developer's PC. It gathers
the target repo's current state through the developer's own `gh` login, with
read-only calls only, and works out what is missing. `--check` prints the plan and
changes nothing. Later tickets carry the plan out (64–66). The plan is computed by
a **pure function** from a facts object, so every combination is tested without
`gh`.

## Acceptance criteria

Write the tests first in a new `tests/test_add_repo.py` and watch each fail before
writing `src/ticket_engine/add_repo.py`. Tests fake `gh` with a recorder that maps
an exact argument list to a scripted `(code, output)`; the PIPELINE_TOKEN probe is
a `MagicMock` `GitHubClient`. The secrets file is a real file in `tmp_path`.

- [x] **The command exists.** `pyproject.toml` `[project.scripts]` gains
  `add-repo = "ticket_engine.add_repo:main"`. `main(argv)` takes the positional
  `repo` (`owner/name`) and the flags `--check`, `--no-jules`,
  `--secrets-file PATH` (default `Path.home() / "secrets.env"`) and
  `--engine-repo` (default `"ilegault/ticket-engine"`). `load_secrets_file(path) -> dict[str, str]`
  reads `KEY=VALUE` lines, ignoring blank lines and `#` comments and stripping one
  pair of surrounding quotes. A missing file, or one without `PIPELINE_TOKEN`,
  prints one line naming the path (never a value) and returns 2. Tests for both,
  and `test_load_secrets_file_parses_comments_and_quotes`.
- [x] **Facts come from read-only `gh` calls.** `gather_facts(repo, engine_repo, gh, probe) -> RepoFacts`
  where `gh: GhRunner = Callable[[list[str], str | None], tuple[int, str]]`
  (args, stdin). It calls exactly: `gh api repos/{repo}` (JSON: `default_branch`,
  `allow_auto_merge`, `security_and_analysis.secret_scanning.status`,
  `security_and_analysis.secret_scanning_push_protection.status`);
  `gh api repos/{repo}/actions/secrets --paginate --jq .secrets[].name`;
  `gh api repos/{repo}/labels --paginate --jq .[].name`;
  `gh api repos/{repo}/rulesets --jq .[].name`;
  `gh api repos/{repo}/actions/permissions` (JSON: `enabled`, `allowed_actions`);
  `gh api repos/{repo}/contents/.ticket-engine.toml -H "Accept: application/vnd.github.raw"`
  (a non-zero exit whose output contains `404` means no config);
  `gh pr list --repo {repo} --head engine/add-repo --state open --json number --jq .[0].number`;
  `gh api repos/{engine_repo}/contents/engine-repos.toml -H "Accept: application/vnd.github.raw"`
  (parsed with `parse_repo_list`); and
  `gh pr list --repo {engine_repo} --head engine/add-{name} --state open --json number --jq .[0].number`.
  `pipeline_token_ok = probe.can_read_variables(repo)`, where `probe` is a
  `GitHubClient` built from the secrets file's `PIPELINE_TOKEN`. Test
  `test_gather_facts_makes_only_read_calls`: every recorded `gh api` call has no
  `-X`/`--method`, and every other call is `gh pr list`.
- [x] **The plan is pure.** In `add_repo.py`, add `@dataclass(frozen=True) class ActionsPermissionsOp`
  (no fields), `class StepKind(str, Enum)` with `adopt_pr`, `upgrade_pr`, `list_pr`,
  `wait_adopt`, `dry_run`, `jules_script`, and
  `Step = SetSecretOp | CreateLabelOp | EnableAutoMergeOp | EnableSecretScanningOp | EnablePushProtectionOp | ActionsPermissionsOp | CreateRulesetOp | StepKind`
  (the op classes are the existing ones in `bootstrap.py`).
  `plan_add_repo(facts: RepoFacts, no_jules: bool) -> AddRepoPlan`
  (`blocked: str | None`, `steps: list[Step]`). If `pipeline_token_ok` is False:
  `blocked = f"PIPELINE_TOKEN cannot reach {repo}: add the repo to that token at https://github.com/settings/personal-access-tokens"`
  and no steps. Otherwise steps, in this order and only when needed: each
  operation `github_setup(...)` returns except `CreateRulesetOp`; `ActionsPermissionsOp`
  (Actions disabled or `allowed_actions != "all"`); `adopt_pr` (no config on the
  default branch and no open adopt PR) or `upgrade_pr` (config present and
  `config_upgrade` returns text, no open adopt PR); `list_pr` (repo not in the
  list and no open list PR); `wait_adopt` (no config on the default branch);
  `CreateRulesetOp` (ruleset missing); `dry_run`; `jules_script` (unless
  `no_jules` or the config says `jules_enabled = false`). One test per rule, and
  `test_fully_wired_repo_plans_only_dry_run_and_jules_script`.
- [x] **Config upgrade is a pure text edit.** `config_upgrade(text: str, no_jules: bool) -> str | None`
  removes every `box_enabled = ...` line, appends any of `python_version = "3.12"`,
  `install = "pip install -e .[dev]"` and `jules_enabled = true` that are missing
  (`false` when `no_jules`, which also replaces an existing `jules_enabled = true`),
  keeps every other line and comment, and returns None when nothing changes. The
  result always parses with `tomllib`. Tests for each change and the no-change case.
- [x] **`--check` prints the plan and changes nothing.** `main([repo, "--check", ...])`
  prints one line per step (the step kind and, for settings, the label or secret
  **name**), or `Nothing to do: {repo} is fully wired.` when the only steps are
  `dry_run` and `jules_script`, or the `blocked` line; it returns 0 (1 when
  blocked). Test `test_check_runs_no_write_call_and_prints_no_secret_value` (the
  fake secrets file holds `PIPELINE_TOKEN=sekrit-value`; assert it is not in the
  captured output and no recorded call writes).

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Doing any step (tickets 64–66). Without `--check`, `main` prints the plan and
  `not implemented yet` and returns 1 until ticket 64.
- The box's token: only the box can check it (ticket 61).

## Comments

2026-10-06: Implemented `add-repo --check` end-to-end:
- Added console script `add-repo` in `pyproject.toml` pointing to `ticket_engine.add_repo:main`.
- Implemented `load_secrets_file` for parsing secrets while keeping secrets safe and validating paths.
- Implemented `gather_facts` using only read-only `gh` calls and token-reach probe on `GitHubClient`.
- Implemented `config_upgrade` pure function to clean and bring `.ticket-engine.toml` up to date.
- Implemented `plan_add_repo` pure core generating `AddRepoPlan` with deterministic ordered steps.
- Implemented CLI `--check` reporting formatted steps or fully wired confirmation, returning 0/1/2 cleanly.
- Tests in `tests/test_add_repo.py` (27 tests) cover each criterion, privacy preservation, and all edge cases.
