# Spec: tests-first gate holes

`scripts/check_tests_first.py` is the tests-first gate (ADR 0001 §2.4–2.5). This repo
holds the canonical copy; `bootstrap` writes it byte for byte into every adopted repo
(`bootstrap.py`, criterion 2b), and RBL, Slackbot and TDS-T8 each carry a copy.

## Problem

Found while unblocking an RBL PR whose only `src/` change was a docstring:

1. **It fails open.** When git cannot work out what changed (shallow checkout, no
   `origin/master`, not a repo), `get_git_changed_files` falls through its candidate
   commands and returns `[]`, and an empty list passes. `--repo-root` pointed at a
   non-git folder prints `OK: No application source files ('src/') modified.`
   Bootstrap's own CI template (`_CI_WORKFLOW_TEMPLATE`) checks out with depth 1, so in
   a repo it creates the gate passes everything.
2. **Commit-message tags are ignored on PR runs.** `main` reads commit messages only
   `if not all_texts`, and a PR always has a title.
3. **One tag exempts everything after it.** A tag in any commit message in the range
   exempts every source file, including code added later by untagged commits.
4. **`src/` is hard-coded.** `.ticket-engine.toml`'s `source_paths` is ignored, so in
   TDS-T8 (code in `t8_daq_system/`) the canonical copy would pass every change.
5. **A docstring or comment edit counts as code** and needs a tag.
6. **PR text and labels only count if present when the run started.** Editing the
   body or adding a label starts no run, and a re-run reuses the old event payload.

## Decisions

- D1. The check fails, never passes, when it cannot determine the range or the
  changed files. CI must check out full history.
- D2. Source folders come from `source_paths` in `.ticket-engine.toml` (default `src`).
- D3. A `.py` file whose syntax tree is unchanged once docstrings are removed is not a
  code change (comments never reach the tree). It needs no test and no tag.
- D4. Escapes have two reaches. PR title/body tags, PR labels and the CLI flags
  (`--message`, `--label`, `--exempt-reason`) cover the whole run. A commit-message tag
  covers a source file only when every commit in the range that changed the file
  carries a tag.
- D5. Commit messages are always read in git mode.
- D6. Every repo's tests workflow re-runs on PR `edited`, `labeled` and `unlabeled`.
- D7. Target repos take the canonical file unedited; the canonical file is stdlib-only
  and keeps every line at 88 characters or fewer so it passes each repo's ruff.

## Tickets

- ticket-engine 87: the canonical script (D1–D5, D7). Held.
- ticket-engine 88: bootstrap's CI template (full history, D6) and this repo's `ci.yml` (D6). Held.
- RBL 105, Slackbot 149, TDS-T8 50: take the canonical file, D6 in `tests.yml`, ADR 0001
  and `AGENTS.md` wording. Held. Each waits on 87 being merged to `master` here.
