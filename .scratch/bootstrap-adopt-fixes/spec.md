# Bootstrap adopt fixes — spec

Status of this document: findings and decisions from the planner, 2026-10-06.
Input for `/ticket-set`. Every decision below is made; do not reopen them in the
tickets.

## Why

The developer ran **bootstrap adopt** (`run_adopt` in
`src/ticket_engine/bootstrap.py`) on the target repo TDS-T8. They also read RBL
ahead of adopting it, and compared both with Slackbot, which was adopted earlier
and then hand-fixed. Adopt produced files that would silently weaken a target
repo, or that needed the same hand edits every time. TDS-T8 has been fixed by
hand on its `engine-adopt` branch. RBL has not been adopted yet. Finding 2 below
would damage RBL if adopt ran on it today.

## Findings and decisions

### 1. Adopt replaces a working tests-first script with one that checks nothing

`adopt()` criterion 2b overwrites any existing `scripts/check_tests_first.py`
with the engine's canonical copy. The canonical script treats only `src/` as
application source (`norm.startswith("src/")`). TDS-T8's code lives in
`t8_daq_system/`. After adopt, no PR ever "touches application source", so the
tests-first gate passes everything. ADR 0001's guard is switched off, silently.

**Decision:** adopt never overwrites an existing `scripts/check_tests_first.py`.
When one exists and differs from the canonical copy, adopt appends a `FileDiff`
(the same mechanism `_handle_template_file` uses for developer-edited files) and
writes nothing. When none exists, adopt writes the canonical copy, as today.

### 2. Adopt overwrites every skill in the repo, not just the ticket skill

`_collect_skill_files` collects every `SKILL.md` under `.agents/skills/` and
`.claude/skills/`. Criterion 2a then replaces each one with
`TICKET_SKILL_POINTER`. RBL has `.claude/skills/rbl-ticket/SKILL.md` and
`.claude/skills/setup-matt-pocock-skills/SKILL.md`. The second has nothing to do
with tickets and would be destroyed.

**Decision:** criterion 2a replaces a `SKILL.md` only when its parent directory
name is `ticket` or ends with `-ticket` (so `rbl-ticket` is replaced, as the
phase-1 design intended). Every other skill file is left untouched and is not
listed in `writes`.

### 3. A missing canonical script becomes an empty tests-first script

`run_adopt` looks for the canonical script at
`pathlib.Path(__file__).parent.parent.parent / "scripts" / "check_tests_first.py"`.
If it isn't there, as in any non-editable install, it uses `""`. Adopt then writes
an empty `scripts/check_tests_first.py` into the target repo. There is also no
command to run adopt. The developer had to discover the
`PYTHONPATH=<engine>/src py -c "..."` incantation.

**Decision:**
- `run_adopt` raises `FileNotFoundError` naming the path it looked for when the
  canonical script is missing and `canonical_tests_first_content` was not passed.
  It never writes an empty script.
- Add a console script `bootstrap-adopt = "ticket_engine.bootstrap:adopt_main"`
  to `[project.scripts]` in `pyproject.toml`. `adopt_main` takes one positional
  `repo_dir` and `--engine-version` (default `v1`). It calls `run_adopt`, then
  prints one line per write (`wrote <path>`), one per diff
  (`left alone, differs from canonical: <path>`) and one per PII finding
  (`PII: <path>:<line> <pattern_name>`, never the matched text). It exits 1 if
  there are PII findings, else 0.

### 4. The generated integrity caller is missing what every repo needs

`caller_integrity_workflow` emits no `permissions:` block and no `python-version`.
Slackbot's copy was hand-edited to add both. TDS-T8 needed the same edit. Without
`python-version`, the reusable workflow defaults to `3.12`. Both new target repos
run 3.14.

**Decision:**
- `caller_integrity_workflow(engine_version, python_version: str | None)` emits
  the job-level block:
  `permissions: {contents: read, pull-requests: write, statuses: write}`. Write
  it as YAML lines exactly as in Slackbot's `.github/workflows/integrity.yml`.
- When `python_version` is not None, it emits `with: python-version: "<v>"`.
- `AdoptInput` gains `python_version: str | None`.
- `run_adopt` fills it from the first match of the regex
  `python-version:\s*["']?(\d+\.\d+)` across the repo's
  `.github/workflows/*.yml`, excluding `dispatch.yml` and `integrity.yml`, read in
  sorted filename order. It is `None` when nothing matches.

### 5. `box_enabled` is easy to put in the wrong place, and nothing notices

`ENGINE_CONFIG_TEMPLATE` has no `box_enabled` line. In Slackbot the developer
added `box_enabled = true` after the `[test_env]` table, so TOML reads it as
`test_env.box_enabled`. `load_repo_config` accepts that silently. Two things go
wrong: the dispatcher sees `box_enabled = False` and never treats Jules as
overflow, and the integrity gate gets a bogus `box_enabled` test environment
variable.

**Decision:**
- `ENGINE_CONFIG_TEMPLATE` gains a top-level line, above any table:
  `box_enabled = false  # true once the box works this repo (ADR 0006)`.
- `load_repo_config` raises `ValueError` when any `[test_env]` value is not a
  string. The message names the key and says to move it above the `[test_env]`
  table if it is a setting.
- **Prerequisite, for the developer:** fix Slackbot's `.ticket-engine.toml` (move
  `box_enabled = true` above `[test_env]`) before the engine tag `v1` moves to
  include this. Otherwise Slackbot's dispatch fails.

### 6. The AGENTS.md section adopt appends carries engine-only rules

`AGENTS_MD_IMPLEMENTATION_SECTION` includes two bullets that describe the
ticket-engine repo itself, not a target repo:
- "Cores are pure: no I/O in dispatch, integrity, or bootstrap cores."
- "Every tunable lives in `.ticket-engine.toml`, never hardcoded in logic."

The second can lead a worker in a target repo to put application settings in the
engine config.

**Decision:** remove both bullets from `AGENTS_MD_IMPLEMENTATION_SECTION`. Keep
the other three. ticket-engine's own `AGENTS.md` is not generated by adopt and is
not touched. Slackbot and TDS-T8 already contain the old section. The developer
trims them by hand; adopt will report them as a diff, which is the existing
behaviour.

### 7. The generated Jules setup script leaves the working tree dirty

`_build_jules_setup_script` runs `pyenv local <version>`, which writes a
`.python-version` file into the repo. Jules verifies the snapshot with
`git status`, sees an untracked file, and fails ("Working tree is dirty") even
though every test passed. This happened on TDS-T8. The script also runs
`pip install -e .[dev]`, which leaves out packages that a repo lists only in
`requirements-dev.txt`.

**Decision:** `_build_jules_setup_script` emits `pyenv global <version>` instead
of `pyenv local`. When the repo has a `requirements-dev.txt`, it emits
`python -m pip install -r requirements-dev.txt` in place of the `pip install -e`
line. Add a final line, `pytest --tb=short -q`, so the snapshot proves the suite
runs.

## Out of scope

- The integrity gate's runner OS. It runs on `ubuntu-latest`. TDS-T8 and RBL
  have Windows CI and Windows-leaning requirements (`pyautogui`, `pygetwindow`,
  `PySide6`, `labjack-ljm`). Whether their suites install and run on Linux is
  unverified. If they don't, making the reusable integrity workflow's runner
  configurable is a separate, held (`.github/`) change that needs its own design
  conversation first.
- Staggering the generated dispatch cron minute (TDS-T8 was hand-set to `37`;
  Slackbot is `27`).
- `bootstrap new` mode, except where it shares a function changed above
  (`caller_integrity_workflow`, `ENGINE_CONFIG_TEMPLATE`,
  `AGENTS_MD_IMPLEMENTATION_SECTION`). Its tests must keep passing.
- Any change in a target repo. Those are the developer's.

## Binding

- ADR 0001 (tests-first; finding 1 exists to protect it)
- ADR 0002 (PII output prints pattern names, never matched text)
- ADR 0005 (tickets landable through the integrity gate)
- ADR 0006 (`box_enabled`)

Gates (ticket-engine CI, in order): `ruff check .`,
`python scripts/check_tests_first.py`, `pytest -q`.

Relevant tests to extend: `tests/test_bootstrap_adopt.py`,
`tests/test_bootstrap_new.py`, and `tests/test_config.py` (create it if absent) for `load_repo_config`.
