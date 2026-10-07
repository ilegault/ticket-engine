# 83: The shared `gate.yml` workflow runs a repo's gate list in CI

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

**Blocked by:** 69

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0011 rules 4 and 5; ADR 0002

## What to build

ADR 0011 rule 4: a target repo's own checks are exactly its `gate_commands`, and CI
runs them through one shared engine workflow, the way it already runs the
integrity gate through `.github/workflows/integrity.yml`. After this ticket the
engine has `.github/workflows/gate.yml`, a reusable workflow (`on: workflow_call`)
whose one job is named `gate`. It:

1. checks out the caller with `actions/checkout@v4`;
2. reads `python_version` and `install` from the caller's `.ticket-engine.toml`
   with the runner's own `python3` and `tomllib`, into step outputs (defaults
   `3.12` and `pip install -e .[dev]`, matching `RepoConfig`);
3. sets up that Python with `actions/setup-python@v5`;
4. runs `install`;
5. installs the engine the way `integrity.yml` does
   (`pip install "ticket-engine @ git+https://github.com/ilegault/ticket-engine.git@v1"`);
6. runs `engine-gate` (ticket 69), which runs every gate command with `[test_env]`
   and fails the job if any failed.

A caller job named `gate` makes the check's name `gate / gate`. This ticket
changes `.github/`, so the integrity gate holds it for the developer, and nothing
depends on it. It reaches target repos when the developer moves the `v1` tag.

## Acceptance criteria

Write the tests first, in a new `tests/test_gate_workflow.py`, and watch each fail
before adding the workflow. Tests read the workflow file as text, as
`tests/test_bootstrap_adopt.py` reads caller workflows; nothing is faked.

- [ ] **Reusable, one job named `gate`.** Test `test_gate_workflow_is_reusable_with_one_gate_job`: the file contains `workflow_call:`, a job with `name: gate`, and no other job.
- [ ] **It builds the repo's environment from its config.** Test `test_gate_workflow_reads_python_version_and_install_from_the_repo_config`: the file mentions `.ticket-engine.toml`, `tomllib`, `python_version`, `install`, and `actions/setup-python@v5`.
- [ ] **It runs the engine's gate.** Test `test_gate_workflow_installs_the_engine_and_runs_engine_gate`: the file contains the `pip install "ticket-engine @ git+https://github.com/ilegault/ticket-engine.git@v1"` line and a step whose `run:` is `engine-gate`.
- [ ] **Run once on a real target.** After merge and a `v1` move, a Slackbot PR shows a `gate / gate` check that runs every gate command. (by hand)

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
