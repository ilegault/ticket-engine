# 87: The tests-first check fails closed, reads `source_paths`, scopes commit tags to the files they cover, and ignores docstring-only edits

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/tests-first-gate-holes/spec.md` (D1–D5, D7)
**Binding:** ADR 0001 (§2.4, §2.5), ADR 0005, ADR 0013

**Read first:** `scripts/check_tests_first.py` (all of it: `categorize_files`,
`find_escape_reasons`, `evaluate_tests_first`, `extract_event_info`,
`get_git_changed_files`, `get_git_commit_messages`, `main`);
`src/ticket_engine/config.py` (`RepoConfig.source_paths`, `default_branch`, and how
`.ticket-engine.toml` is read); `src/ticket_engine/bootstrap.py` (criterion 2b: the
file is copied byte for byte into target repos).

## What to build

`scripts/check_tests_first.py` is copied unedited into target repos, so it stays
**stdlib-only** and **every line is 88 characters or fewer** (TDS-T8's ruff uses the
88 default). Keep these names importable with their current signatures and, for the
default source path, their current return values and message text:
`normalize_path`, `categorize_files`, `find_escape_reasons`, `evaluate_tests_first`,
`extract_event_info`, `main`. RBL's test suite imports them.

1. **Repo settings.** New `load_repo_settings(repo_root: pathlib.Path) ->
   RepoSettings` (frozen dataclass: `source_paths: tuple[str, ...]`,
   `default_branch: str | None`). It reads `<repo_root>/.ticket-engine.toml` with
   `tomllib`: `source_paths` (default `["src"]`) and `default_branch` (default
   `None`). Each source path is normalized with `normalize_path` and given exactly one
   trailing `/`. A missing file gives the defaults. A file that `tomllib` cannot parse
   makes `main` print `FAIL: cannot read .ticket-engine.toml: <error>` and return 1.
2. **Source paths everywhere.** `categorize_files(files, source_paths=("src/",))` and
   `evaluate_tests_first(changed_files, escape_reasons=None, source_paths=("src/",))`
   gain the keyword. A file is a source file when it starts with any source path. In
   every message, the literal `'src/'` becomes the source paths, each in single quotes,
   joined with `, ` (so the default still reads `'src/'`). In the FAIL message, the ADR
   line becomes exactly `Tests-first rule violation (see ADR 0001 under docs/adr/):`
   because target repos name that file differently.
3. **The range (git mode, i.e. `--files` not given).** Delete
   `get_git_changed_files` and `get_git_commit_messages` and their fall-through
   candidate lists (nothing imports them). Resolve, in order:
   - head: `--head`, else the event's `pull_request.head.sha` (new
     `extract_event_head(event_path) -> str | None`, same file handling as
     `extract_event_info`), else `HEAD`;
   - base: `--base`, else the event's `pull_request.base.sha` (from
     `extract_event_info`), else `origin/<default_branch>` when the settings have one,
     else the first of `origin/master`, `origin/main` for which
     `git rev-parse --verify --quiet <ref>^{commit}` succeeds;
   - `range_base`: `git merge-base <base> <head>`. If it equals
     `git rev-parse <head>` (a push to the base branch itself), `range_base` is
     `<head>~1`; if `<head>~1` does not resolve (root commit), print
     `OK: nothing to compare (root commit).` and return 0.
   If no base resolves, or any git command in this step or step 4 exits non-zero, print
   `FAIL: cannot determine the changes to check: <command> exited <code>: <first stderr line>. CI must check out full history (actions/checkout with fetch-depth: 0).`
   and return 1. When no base resolves, `<command>` is the last `git rev-parse` tried.
   Long lines are built from shorter string pieces to keep the 88-character limit.
4. **Changed files and commits.** Changed files: `git diff --name-only <range_base>
   <head>`. A file under `tests/` means tests changed. For each changed source file:
   the commits that touched it are
   `git log --format=%H%x00%s%x00%B%x1e <range_base>..<head> -- <path>` (default
   history simplification; merges are included whenever git lists them). Commit
   messages are always read in git mode, whatever event text exists.
5. **Docstring- and comment-only files.** New pure `code_fingerprint(source: str) ->
   str | None`: `ast.parse` the source; from the body of the `Module` and of every
   `ClassDef`, `FunctionDef` and `AsyncFunctionDef`, remove the first statement when it
   is an `ast.Expr` whose value is an `ast.Constant` holding a `str`; return
   `ast.dump(tree)` with default arguments (no line numbers). A `SyntaxError` returns
   `None`. A changed source file is **non-code** only when its path ends in `.py`,
   `git show <rev>:<path>` succeeds for both `range_base` and `head`, and both
   fingerprints are equal and not `None`. Every other changed source file (added,
   deleted, renamed, not `.py`, unparseable, `git show` failing) is **code**. This
   step's `git show` failures mean "code", not the step-3 FAIL. `code_fingerprint` is
   a pure function of a string, outside any git call, so it is tested on strings.
6. **The decision is pure.** New frozen dataclasses `CommitInfo(sha: str, subject:
   str, escape_reasons: tuple[str, ...])` (reasons from `find_escape_reasons` on that
   commit's message alone) and `SourceChange(path: str, is_code: bool, commits:
   tuple[CommitInfo, ...])`, and `decide_tests_first(changes, tests_changed: bool,
   run_escapes: Sequence[str], source_paths: Sequence[str]) -> tuple[bool, str]`. It
   does no I/O so every rule below is tested without git. In order:
   1. no change with `is_code` → pass:
      `OK: No code changes under <paths>.` plus, when any non-code file exists,
      ` Docstring/comment-only: <paths, comma-separated>.`
   2. `tests_changed` → pass with the existing "accompanied by test changes" text.
   3. `run_escapes` non-empty → pass with the existing `OK (EXEMPT): ...` text. Run
      escapes are PR title/body tags and PR labels from the event, plus `--message`,
      `--label` and `--exempt-reason`.
   4. every code change has at least one commit and every one of its commits has
      `escape_reasons` → pass: `OK (EXEMPT): every commit that changed these source
      files carries an exemption tag:` then one `  - <path>: <reason>` line per file.
   5. otherwise FAIL: the existing FAIL text (with the step-2 changes), but the file
      list is only the uncovered code files, and under each, one
      `      untagged commit <first 7 of sha> <subject>` line per commit without a tag.
7. **`--files` mode** keeps today's behaviour, with `source_paths` from the settings:
   `evaluate_tests_first(args.files, escape_reasons=..., source_paths=...)`. No git
   call, no docstring check: every listed source file counts as code.
8. **`AGENTS.md`**, the paragraph starting `` `scripts/check_tests_first.py` fails any
   change to `src/` `` (end of §9): replace it with exactly:

   > `scripts/check_tests_first.py` fails any change to a source folder
   > (`source_paths`, default `src/`) that does not also touch `tests/`. A `.py` file
   > whose code is unchanged once docstrings and comments are removed does not count.
   > A tag in a commit message (`[no-test-needed: <reason>]`) covers only the files
   > that commit changed, and a file is exempt only when every commit that changed it
   > is tagged; a tag in the PR title or body, or the `tests-exempt` label, covers the
   > whole PR. The check fails when it cannot work out what changed. Escapes are
   > visible in history and in a target repo force a merge hold.

## Acceptance criteria

Write the tests first in a new `tests/test_check_tests_first.py` and watch each fail.
Load the script with `importlib.util.spec_from_file_location` from
`scripts/check_tests_first.py`. Git tests build a **real** repo in `tmp_path`
(`git init -b master`, set `user.name` and `user.email` locally, real commits) and set
`refs/remotes/origin/master` with `git update-ref`. Nothing is faked: no
`subprocess` patching. Call `main([..., "--repo-root", str(repo)])` and read stdout
with `capsys`; `monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)` in each.

- [ ] `test_unresolvable_base_fails_closed`: a repo with one commit changing
  `src/a.py` and no `origin/*` ref → `main` returns 1 and stdout contains
  `FAIL: cannot determine the changes to check` and `fetch-depth: 0`. A `tmp_path`
  folder that is not a git repo also returns 1 with the same text.
- [ ] `test_commit_tag_is_read_on_a_pr_run`: an event file with a non-empty
  `pull_request.title` and `body` (no tag), `base.sha` = origin/master's commit,
  `head.sha` = the branch tip; the one branch commit changes code in `src/a.py` and its
  message has `[no-test-needed: bench only]` → returns 0 and stdout contains
  `bench only`.
- [ ] `test_later_untagged_commit_is_not_covered`: commit 1 (tagged) and commit 2
  (untagged) both change code in `src/a.py` → returns 1; stdout contains `src/a.py`
  and `untagged commit <commit 2's first 7>` and not commit 1's first 7. Second case in
  the same test: commit 1 (tagged) changes `src/a.py`, commit 2 (untagged) changes
  `src/b.py` → returns 1 and the FAIL file list names `src/b.py` and not `src/a.py`.
- [ ] `test_docstring_and_comment_only_change_passes`: one untagged commit that only
  rewords a function docstring and adds a `#` comment in `src/a.py` → returns 0 and
  stdout contains `Docstring/comment-only: src/a.py`. And `test_code_fingerprint`:
  equal for two sources differing only in module, class and function docstrings and
  comments; different when a constant changes; different for `def f():\n    "d"\n`
  versus `def f():\n    pass\n`; `None` for `def f(:`.
- [ ] `test_source_paths_come_from_engine_config`: a repo with `.ticket-engine.toml`
  holding `source_paths = ["t8_daq_system"]` and an untagged code change to
  `t8_daq_system/x.py` → returns 1 and stdout names `t8_daq_system/x.py`. A repo with
  no config and the same change under `lib/x.py` → returns 0. A config file with
  invalid TOML → returns 1 with `FAIL: cannot read .ticket-engine.toml`.
- [ ] `test_push_to_base_branch_checks_the_last_commit`: `origin/master` equals
  `HEAD`, and the last commit is an untagged code change to `src/a.py` → returns 1.
- [ ] `test_files_mode_is_unchanged`: `--files docs/README.md tests/test_foo.py` → 0;
  `--files src/x.py` → 1; `--files src/x.py tests/test_x.py` → 0; with
  `--exempt-reason r` → 0; with `--message "m [no-test-needed: n]"` → 0; with
  `--label tests-exempt` → 0. `evaluate_tests_first(["src/x.py"])` returns a message
  containing `FAIL: Application source files modified under 'src/' without
  corresponding test changes under 'tests/'`.
- [ ] `test_script_fits_every_target_repo`: every line of
  `scripts/check_tests_first.py` is 88 characters or fewer, and every module it imports
  (read with `ast`) is in `sys.stdlib_module_names`.
- [ ] `AGENTS.md` §9 carries the step-8 paragraph verbatim. (no test: documentation)

The integrity gate's check 7 swaps only `source_paths` (`src/`) back to base, and this
file is under `scripts/`, so check 7 will report that the new tests pass on base. That
hold is expected: this ticket is `Auto-merge: no`. Do not change the tests to make
check 7 pass.

## Gate

Run all three, in CI's order (`.github/workflows/ci.yml`). All must pass.

```
ruff check .
python scripts/check_tests_first.py
pytest -q
```

## Comments
