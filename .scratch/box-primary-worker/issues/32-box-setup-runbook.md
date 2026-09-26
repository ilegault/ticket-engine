# 32: Box setup runbook

**What to build:** A runbook the developer can follow to rebuild the box from a clean Windows install to a running `box-worker`, written from the code as it stands after ticket 31. Spec: `.scratch/box-primary-worker/spec.md`. ADR 0006, ADR 0007.

**Blocked by:** 31

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

The runbook is `docs/box-setup.md`, which is not a held path. It is checked by a test in a new `tests/test_box_setup_doc.py` that reads the file. The test also imports `LocalWorkerConfig` and `load_local_config`, so the doc cannot drift from the config's real keys. Write the test first and watch it fail.

- [x] **Sections, in order**, each a `## ` heading:
  1. `The agent account` (standard, non-admin; the developer's storage folder denied to it)
  2. `Python and the engine checkout`
  3. `agy login` (one interactive login under `agent`)
  4. `GitHub token` (fine-grained; Contents, Pull requests and Issues read/write, and Variables read, on the target repos; Issues read/write on the engine repo; no Workflows, no Administration; expiring)
  5. `Local config`
  6. `Start at boot` (a Task Scheduler task running `box-worker` as `agent` at startup, restarting on failure)
  7. `Checking it works`

  The test asserts all seven headings in this order.
- [x] **Config example is real.** The `Local config` section contains one fenced `toml` block. The test extracts it, writes it to `tmp_path`, parses it with `tomllib` and asserts it has a key for every field of `LocalWorkerConfig` except `repos` and `github_token`, in the layout `load_local_config` reads. It then loads the same file with `load_local_config` and asserts `repos` has at least one entry.
- [x] **No secrets, no names.** The test asserts the doc contains no string matching `gh[pousr]_[A-Za-z0-9]{10,}` or `github_pat_`. `Roles, not people`: the doc says "the developer", never a person's name.
- [x] **Commands are the real ones.** The doc names the console script `box-worker` and the flag `--once`. The test asserts both appear, and that `box-worker` is a key in `pyproject.toml`'s `[project.scripts]` (read with `tomllib`).

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

2026-09-26: Wrote `docs/box-setup.md` as the developer's runbook from a clean Windows install to a running `box-worker`, proved by new `tests/test_box_setup_doc.py`.
- AC1: seven `## ` headings in the required order (The agent account, Python and the engine checkout, agy login, GitHub token, Local config, Start at boot, Checking it works); tested in `test_headings_appear_in_order`.
- AC2: the `Local config` section's one fenced `toml` block covers every `LocalWorkerConfig` field except `repos`/`github_token` (nested fields under `[agy]`, matching `load_local_config`'s layout), and `load_local_config` on the extracted file yields at least one repo entry; tested in `test_config_example_covers_every_local_worker_config_field`.
- AC3: no `ghp_`/`gho_`/`ghu_`/`ghs_`/`ghr_`-style token pattern or `github_pat_` string anywhere in the doc; "the developer" used throughout, never a name; tested in `test_doc_contains_no_secrets_and_says_the_developer_not_a_name`.
- AC4: the doc names `box-worker` and `--once`, and `box-worker` is a real key in `pyproject.toml`'s `[project.scripts]`; tested in `test_doc_names_the_real_console_script_and_flag`.
- Gates: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q` (552 passed) all green on Python 3.12. Mutation checked by hand: deleted the `concurrency = 1` line from the doc's config example; `test_config_example_covers_every_local_worker_config_field` went red naming the missing key; restored and reverified green.
