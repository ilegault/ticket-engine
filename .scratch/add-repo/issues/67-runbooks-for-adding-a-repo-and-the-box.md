# 67: Runbooks: adding a repo, and the box without a repo list

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 62, 66

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0009; ADR 0010

## What to build

The two docs a developer follows: a new `docs/adding-a-repo.md` (what `add-repo`
does and the clicks left), and `docs/box-setup.md` brought in line with a box that
clones and provisions repos itself. Tests keep both docs tied to the real code, the
way `tests/test_box_setup_doc.py` already does.

## Acceptance criteria

Write the tests first and watch each fail before changing the docs.

- [ ] **`docs/adding-a-repo.md` (new).** Sections, in order: `## Before you start`
  (`gh` logged in as the repo admin; the secrets file and its two keys by name);
  `## Run it` (`add-repo owner/name`, `--check`, `--no-jules`, `--no-box`,
  `--secrets-file`); `## What is left for you` (add the repo to `PIPELINE_TOKEN`
  and to the box's token, merge the adopt PR, merge the repo-list PR, paste the
  Jules script); `## On the box` (nothing to do; what *not ready* means and where
  it shows; `BOX_PAUSED` on the engine repo pauses the whole box; `box = false`
  in `engine-repos.toml` for one repo). New `tests/test_adding_a_repo_doc.py`:
  `test_sections_in_order`; `test_every_add_repo_flag_is_documented` (parses
  `add_repo.main`'s argparse parser via a `build_parser()` function, adding it in
  `add_repo.py` if `main` builds the parser inline, and asserts each long option
  appears in the doc); `test_no_secrets_no_names` (copy the regexes from
  `tests/test_box_setup_doc.py`).
- [ ] **`docs/box-setup.md` loses the per-repo steps.** In
  `## Python and the engine checkout`, replace the paragraph that tells the
  developer to clone every target repo with: the box clones listed repos into
  `projects_dir` itself; the one manual step is a single `git pull` in any clone
  if git ever asks for a login. Add to `## Python and the engine checkout` that
  each repo's Python (`python_version`) must be installed on the box, with the
  `py` launcher. Add to `## Checking it works`: the status issue's `Not ready:`
  line and its reason codes (all five, by name), `box_readiness.json` in
  `logs_dir`, and `BOX_PAUSED`. Headings stay exactly as
  `REQUIRED_HEADINGS` lists them.
- [ ] **Reason codes in the doc are the real ones.** New test
  `test_box_setup_doc_names_every_not_ready_reason` in
  `tests/test_box_setup_doc.py` asserts each `NotReadyReason` value appears in
  the doc.
- [ ] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are. No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- `AGENTS.md`, `CONTEXT.md`, `docs/adr/` (already written by the planner).
- `docs/agents/issue-tracker.md`.

## Comments
