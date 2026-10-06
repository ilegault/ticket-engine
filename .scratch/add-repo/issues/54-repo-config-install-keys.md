# 54: Target repos declare their Python and install command, and can switch Jules off

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0010 rule 1

## What to build

A target repo's `.ticket-engine.toml` says which Python it needs and how to install
it, and whether Jules may work it. The box (ticket 59) and the Jules setup script
both use the same two keys, so the install recipe is declared once. Today
`_build_jules_setup_script` in `src/ticket_engine/bootstrap.py` hard-codes
`pip install -e .[dev] 2>/dev/null || pip install -e .`.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. The repo config
tests write a real `.ticket-engine.toml` into `tmp_path`.

- [ ] **`RepoConfig` gains three keys.** In `src/ticket_engine/config.py`, add
  `python_version: str = "3.12"`, `install: str = "pip install -e .[dev]"` and
  `jules_enabled: bool = True` to `RepoConfig`, and read each in
  `load_repo_config` the way `box_silent_hours` is read. Do not touch
  `box_enabled` (ticket 55 retires it). Tests
  `test_repo_config_install_keys_default` and
  `test_repo_config_reads_install_keys_from_toml` (values `"3.11"`,
  `"pip install -r requirements.txt"`, `false`).
- [ ] **The Jules setup script runs the declared install.** Add `install: str = "pip install -e .[dev]"`
  as the last field of `GitHubSetupInput`. `_build_jules_setup_script(python_version, install, system_libraries)`
  writes `# Install project` followed by `install` verbatim, in place of the
  hard-coded line. `github_setup` passes `inp.install`. Test
  `test_jules_setup_script_uses_the_declared_install` asserts the script contains
  `pip install -r requirements.txt` and does not contain `2>/dev/null`. Existing
  tests in `tests/test_bootstrap_github.py` that assert on the script text keep
  passing; if one asserts the old hard-coded line, rewrite it in place under the
  same name to assert the default `pip install -e .[dev]`.
- [ ] **New repos get the keys.** `ENGINE_CONFIG_TEMPLATE` in `bootstrap.py` gains
  the lines `python_version = "3.12"`, `install = "pip install -e .[dev]"` and
  `jules_enabled = true`. Test `test_engine_config_template_declares_install_keys`
  parses the template with `tomllib` and asserts the three values.
- [ ] **Existing tests unchanged.** Apart from any in-place rewrite named above,
  every existing test passes with its assertions as they are. No test is deleted,
  skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Anything on the box (ticket 59) or in the dispatcher (ticket 55).
- Upgrading existing repos' config files (ticket 63's `config_upgrade`).

## Comments
