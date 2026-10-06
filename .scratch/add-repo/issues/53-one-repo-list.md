# 53: One repo list: `engine-repos.toml` entries, a reader, and a token-reach probe

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0009 rules 4 and 6; ADR 0002

## What to build

`engine-repos.toml` becomes the single list of target repos (the *repo list*).
Today it is `repos = ["owner/name", ...]` and only the morning report reads it,
inline in `scripts/run_morning_report.py`. After this ticket it holds `[[repos]]`
tables, one pure function parses it, one function fetches it from GitHub, and the
morning report uses the parser. This ticket also adds the probe the box and
`add-repo` both use to check that a token can reach a repo.

## Acceptance criteria

Write the tests first, in a new `tests/test_repo_list.py` (plus the one probe test
in `tests/test_github_adapter.py`), and watch each fail before changing `src/`.
Tests may fake HTTP exactly as `test_get_repo_variable_missing_returns_none_without_an_error_log`
in `tests/test_github_adapter.py` does (`patch("urllib.request.urlopen", side_effect=...)`
with `_http_error`). The parser tests use real TOML text.

- [ ] **The parser.** New module `src/ticket_engine/repo_list.py` with
  `@dataclass(frozen=True) class RepoListEntry` (`repo: str`, `box: bool = True`),
  `class RepoListError(ValueError)`, and pure `parse_repo_list(text: str) -> list[RepoListEntry]`
  that keeps file order. It raises `RepoListError` when: the text is not valid TOML;
  `repos` is a list of strings (message contains `old format`); an entry has no
  `repo`, or `repo` does not match `^[\w.-]+/[\w.-]+$`; `box` is present and not a
  bool; or two entries name the same repo ignoring case (message contains
  `duplicate`). A file with no `repos` key returns `[]`. Test
  `test_parse_repo_list_reads_entries_in_order_with_box_default` and one test per
  rejection, each asserting the exception type and the message fragment.
- [ ] **The file moves to the new format.** Rewrite `engine-repos.toml` at the repo
  root as `[[repos]]` tables, keeping every repo the file lists when you start, in
  the same order, and keeping a comment block that explains the format and that
  `box = false` keeps the box away from a repo (ADR 0009). Test
  `test_engine_repos_toml_parses_and_lists_slackbot` reads the real file and
  asserts `RepoListEntry(repo="ilegault/slackbot", box=True)` is in the result.
- [ ] **The fetcher.** `fetch_repo_list(client: GitHubClient, engine_repo: str) -> list[RepoListEntry]`
  in `repo_list.py` calls `client.get_file_contents(engine_repo, "engine-repos.toml")`
  with no `ref` (the default branch) and returns `parse_repo_list` of its
  `"content"`. Errors propagate. Test with a `MagicMock` client asserting the call
  arguments and the parsed result.
- [ ] **The morning report uses the parser.** Add pure
  `repo_names_for_report(text: str) -> list[str]` to `repo_list.py`: the `repo` of
  every entry, in file order, whatever `box` says. In
  `scripts/run_morning_report.py`, replace `repos_data.get("repos", [])` with
  `repo_names_for_report(repos_config.read_text(encoding="utf-8"))`; a
  `RepoListError` is logged with `logger.error("engine-repos.toml unreadable: %s", exc)`
  and the script returns 1. Test
  `test_repo_names_for_report_includes_box_false_repos`.
- [ ] **The token-reach probe.** `GitHubClient.can_read_variables(repo: str) -> bool`
  in `src/ticket_engine/github.py` sends `GET /repos/{repo}/actions/variables`
  through `self._request(..., expected_codes=(403, 404))`. It returns True on
  success, False on 403 or 404, and re-raises any other `HTTPError`. Copy
  `get_repo_variable`'s structure. Tests
  `test_can_read_variables_true_on_200`,
  `test_can_read_variables_false_on_403_and_404_without_an_error_log` (copy
  `_loud_records`), `test_can_read_variables_raises_on_500`.
- [ ] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are. No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- The box and the dispatcher reading the list (tickets 55, 58).
- `.github/workflows/morning-report.yml`.

## Comments
