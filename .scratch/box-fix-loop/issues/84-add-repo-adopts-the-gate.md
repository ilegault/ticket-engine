# 84: `add-repo` adopts the gate: caller workflow, two required checks, and a warning for other workflows

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 66

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0011 rules 4 and 5; ADR 0009

## What to build

`add-repo` and the bootstrap it drives wire a repo to the engine. After this ticket
every repo they adopt (RBL is next) gets the single gate list of ADR 0011: a caller
workflow for the shared `gate` workflow, a ruleset that requires `gate / gate`
beside the integrity gate, and a warning for any workflow outside the gate. Open
tickets that fail ticket lint already show up in the dispatcher dry run `add-repo`
runs after the adopt merge (ticket 66), so nothing extra is needed for them.

- `src/ticket_engine/bootstrap.py`: a new
  `caller_gate_workflow(engine_version: str) -> str`, shaped like
  `caller_integrity_workflow`: name `Gate`, on `pull_request` (branches
  `[master, main]`) and `push`, one job `gate` that `uses:
  ilegault/ticket-engine/.github/workflows/gate.yml@{engine_version}`. Adopt mode
  writes it to `.github/workflows/gate.yml` next to the dispatch and integrity
  callers (through `_handle_template_file`, as they are), and new mode appends it
  as a `FileWrite` beside them.
- A new constant `_GATE_CHECK_CONTEXT = "gate / gate"` next to
  `_INTEGRITY_CHECK_CONTEXT`. `plan_add_repo` (`src/ticket_engine/add_repo.py`)
  plans the ruleset with `required_checks=(_INTEGRITY_CHECK_CONTEXT, _GATE_CHECK_CONTEXT)`.
- `RepoFacts` gains `workflow_files: list[str]`, gathered in `gather_facts` with
  `gh api repos/{repo}/contents/.github/workflows --jq '.[].name'` (an error or a
  404 → `[]`). `--check` prints one line `warning: workflow <name> is not part of
  the gate; it can never be a required check` for each name other than
  `gate.yml`, `integrity.yml` and `dispatch.yml`.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Bootstrap tests
use the fixtures in `tests/test_bootstrap_adopt.py` and `tests/test_bootstrap_new.py`;
add-repo tests use the recording fake `gh` in `tests/test_add_repo.py`.

- [ ] **Adopt and new both write the gate caller.** Tests `test_adopt_writes_the_gate_caller_workflow` and `test_new_writes_the_gate_caller_workflow`: `.github/workflows/gate.yml` equals `caller_gate_workflow(engine_version)`, which `uses:` `gate.yml@<engine_version>` from a job named `gate`.
- [ ] **An already-adopted repo gets it too.** Test `test_adopt_adds_the_gate_caller_to_an_already_adopted_repo`: a fixture repo that already has the dispatch and integrity callers but no `gate.yml`; adopt writes only `.github/workflows/gate.yml` (and nothing else changes).
- [ ] **The ruleset requires both checks.** Test `test_plan_ruleset_requires_gate_and_integrity`: the planned `CreateRulesetOp` has `required_checks == ("ticket-engine/integrity-gate", "gate / gate")`.
- [ ] **Other workflows are warned about.** Test `test_check_warns_about_workflows_outside_the_gate`: the fake `gh` lists `tests.yml`, `gate.yml`, `integrity.yml`; `--check` output has exactly one warning, naming `tests.yml`.
- [ ] **No workflow folder is fine.** Test `test_check_with_no_workflow_folder_warns_nothing`: the fake `gh` answers 404; no warning and exit code unchanged.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
