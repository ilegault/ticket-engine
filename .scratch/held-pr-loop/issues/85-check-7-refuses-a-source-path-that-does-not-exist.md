# 85: Check 7 says so when none of the repo's `source_paths` exist, instead of reporting "passed on base"

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None

**Binding:** ADR 0001 §2.7; ADR 0013; ADR 0005

## What to build

Check 7 proves a new test needs the PR's change by swapping the repo's source
folders back to the base branch and running the test again. The folders come from
`source_paths` in the target repo's `.ticket-engine.toml`, default `["src"]`
(`RepoConfig` in `src/ticket_engine/config.py`). A target repo whose code lives
elsewhere (TDS-T8 keeps it in `t8_daq_system/`) and never set `source_paths` gets no
swap at all: `temporary_base_source` in `src/ticket_engine/integrity_runner.py`
finds no `src/`, changes nothing, and the "base" run is the PR's own code again.
Every new test then "passes on base", every PR is held with
`Check 7 hold: New test(s) passed on base code`, and the worker is told to fix tests
that were never wrong. On TDS-T8 this held ten PRs for one ticket.

After this ticket the gate refuses to guess, and the dispatcher dry run warns at
adoption time:

1. `BaseTestResults` (`src/ticket_engine/integrity.py`) gains
   `misconfigured_source_paths: list[str] = field(default_factory=list)`.
2. `execute_new_tests_on_base` (`src/ticket_engine/integrity_runner.py`): after the
   "runnable" pass on the PR's own code and before `temporary_base_source`, if no
   entry of `source_paths` exists under `repo_path`, it runs nothing on base and
   returns a `BaseTestResults` with `passed_tests=[]`, `failed_tests=[]` and
   `misconfigured_source_paths=list(source_paths)`.
3. `IntegrityCore.evaluate` check 7 (`src/ticket_engine/integrity.py`, the
   `# 10. Check 7` block): read `misconfigured_source_paths` from both the
   `Mapping` and the attribute form. When it is non-empty: hold, with exactly one
   check-7 reason
   `Check 7 cannot run: none of source_paths (<comma-separated list>) exist in this repo, so the base run would test the PR's own code. Set source_paths in .ticket-engine.toml to the folder(s) that hold the code under test.`
   and no "passed on base" or "Check 7 pass" reason.
4. Check 9 in the same method: when `misconfigured_source_paths` is non-empty it
   passes `failed_on_base_names=None` to `named_test_problems`, so it still checks
   that named tests exist but makes no claim about base.
5. `run_dispatch_dry_run` (`src/ticket_engine/cli.py`): after loading the config,
   if no entry of `config.source_paths` exists under `repo_path`, print a section
   after "Ticket problems":

       Config problems (the integrity gate cannot run check 7):
         - none of source_paths (<list>) exist in <repo_path>; set source_paths in .ticket-engine.toml to the folder(s) that hold the code under test

   The exit code stays 0, as it is for ticket problems.

Partly-missing lists (one folder exists, another does not) are not a config problem
and keep today's behaviour.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Gate-runner tests
build a real git repo in `tmp_path` and run the real gate, as the check-7 tests in
`tests/test_integrity_runner.py` do; core tests call `IntegrityCore().evaluate`
directly, as `test_check9_fails_when_a_named_test_passed_on_base` in
`tests/test_integrity_check9.py` does. Nothing is faked except what those tests
already fake.

- [ ] **The gate names the misconfiguration.** Test `test_check7_holds_with_a_config_reason_when_no_source_path_exists` in `tests/test_integrity_runner.py`: give `_check7_repo` a `code_dir: str = "src"` parameter (default keeps every existing call unchanged) and build the repo with `code_dir="pkg"` and no `.ticket-engine.toml`; the new test imports from `pkg.lib`. Asserts the summary contains `Check 7 cannot run: none of source_paths (src) exist`, contains `HOLD`, does not contain `passed on base`, and the exit code is 0.
- [ ] **The core holds and does not claim a base result.** Test `test_check7_misconfigured_source_paths_holds_without_a_base_verdict` in `tests/test_integrity_core.py`: `base_test_results` is a `Mapping` with `new_tests` and `passed_tests` both `["tests/test_a.py::test_a"]` and `misconfigured_source_paths=["src"]`; asserts the verdict is HOLD, one reason starts `Check 7 cannot run`, and no reason contains `passed on base`.
- [ ] **Check 9 does not fail a PR for the gate's misconfiguration.** Test `test_check9_skips_the_base_claim_when_source_paths_are_misconfigured` in `tests/test_integrity_check9.py`: copy `test_check9_fails_when_a_named_test_passed_on_base`, add `misconfigured_source_paths=["src"]` to its base results; asserts the verdict is HOLD, not FAIL, and no reason contains `did not fail on the base code`.
- [ ] **The dry run warns at adoption.** Test `test_dispatch_dry_run_lists_source_paths_that_do_not_exist` in `tests/test_cli.py`: one `tmp_path` repo with a `.ticket-engine.toml` and no `src/` folder, run through `run_dispatch_dry_run`; output contains `Config problems` and `none of source_paths (src) exist`. A second repo in the same test with a `src/` folder prints no `Config problems`.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
