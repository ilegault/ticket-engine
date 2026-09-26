# 32: Box setup runbook

**What to build:** A runbook the developer can follow to rebuild the box from a clean Windows install to a running `box-worker`, written from the code as it stands after ticket 31. Spec: `.scratch/box-primary-worker/spec.md`. ADR 0006, ADR 0007.

**Blocked by:** 31

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

The runbook is `docs/box-setup.md`, which is not a held path. It is checked by a test in a new `tests/test_box_setup_doc.py` that reads the file. The test also imports `LocalWorkerConfig` and `load_local_config`, so the doc cannot drift from the config's real keys. Write the test first and watch it fail.

- [ ] **Sections, in order**, each a `## ` heading:
  1. `The agent account` (standard, non-admin; the developer's storage folder denied to it)
  2. `Python and the engine checkout`
  3. `agy login` (one interactive login under `agent`)
  4. `GitHub token` (fine-grained; Contents, Pull requests and Issues read/write, and Variables read, on the target repos; Issues read/write on the engine repo; no Workflows, no Administration; expiring)
  5. `Local config`
  6. `Start at boot` (a Task Scheduler task running `box-worker` as `agent` at startup, restarting on failure)
  7. `Checking it works`

  The test asserts all seven headings in this order.
- [ ] **Config example is real.** The `Local config` section contains one fenced `toml` block. The test extracts it, writes it to `tmp_path`, parses it with `tomllib` and asserts it has a key for every field of `LocalWorkerConfig` except `repos` and `github_token`, in the layout `load_local_config` reads. It then loads the same file with `load_local_config` and asserts `repos` has at least one entry.
- [ ] **No secrets, no names.** The test asserts the doc contains no string matching `gh[pousr]_[A-Za-z0-9]{10,}` or `github_pat_`. `Roles, not people`: the doc says "the developer", never a person's name.
- [ ] **Commands are the real ones.** The doc names the console script `box-worker` and the flag `--once`. The test asserts both appear, and that `box-worker` is a key in `pyproject.toml`'s `[project.scripts]` (read with `tomllib`).

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
