# 49: A GitHub answer the code expects is not logged as an error

**Status:** done

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** none. Background is the box's first real run (tickets 45–47 in this effort).
**Binding:** ADR 0007 rule 5 (logs stay on the box)

## What to build

`GitHubClient._request` (`src/ticket_engine/github.py`) logs every
`urllib.error.HTTPError` at ERROR, with GitHub's response body, and then re-raises.
Several callers catch one specific code because it is a normal answer, not a
failure. A 404 from `get_repo_variable` means the variable is not set: the repo is
not paused. A 422 from `create_claim_branch` means the ticket is already claimed.
The box reads `TICKET_ENGINE_PAUSED` on every tick, so its log carries lines like
this on every tick, and they mean nothing:

    ERROR ticket_engine.github: GitHub API HTTPError 404 Not Found: {"message":"Not Found","documentation_url":"https://docs.github.com/rest/actions/variables#get-a-repository-variable","status":"404"}

They bury the real errors.

Give `_request` a set of expected codes. Each caller passes the code it already
handles. Those codes are logged at DEBUG. Every other `HTTPError` is still logged
at ERROR with the body, exactly as now.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests patch
`urllib.request.urlopen` the way the existing tests in
`tests/test_github_adapter.py` do (see
`test_create_claim_branch_already_exists_returns_false`). Each one raises a real
`urllib.error.HTTPError` whose `fp.read()` returns the body. `GitHubClient` itself
is real. Use `caplog.set_level(logging.DEBUG, logger="ticket_engine.github")`.

- [x] **`_request` takes expected codes.** Its signature becomes
  `_request(self, method, endpoint, payload=None, *, expected_codes: tuple[int, ...] = ())`.
  On an `HTTPError` whose `exc.code in expected_codes`, it logs
  `logger.debug("GitHub API %s %s returned expected %s", method, endpoint, exc.code)`
  and re-raises. Any other `HTTPError` is handled exactly as today: one ERROR
  record with code, reason and body, then re-raise. Callers keep their existing
  `try/except` and code checks; only the argument is added.
- [x] **Each caller passes the code it already handles, and no other call site
  changes.** `create_claim_branch` passes `expected_codes=(422,)`. These pass
  `expected_codes=(404,)`: `get_branch_head_time`, `get_repo_variable`,
  `list_claim_branches`, `remove_issue_label`, `delete_branch`, `list_issues`,
  `find_open_pr`, and the first `PATCH` call in `set_repo_variable` (its fallback
  `POST` passes nothing).
- [x] **A missing pause variable leaves no error in the log.** New test
  `test_get_repo_variable_missing_returns_none_without_an_error_log`: urlopen
  raises 404 with exactly the body quoted above.
  `get_repo_variable("o/r", "TICKET_ENGINE_PAUSED")` returns `None`, and no
  record at WARNING or above exists. New test
  `test_create_claim_branch_already_exists_logs_no_error`: a 422 makes
  `create_claim_branch` return `False`, and no record at WARNING or above exists.
- [x] **Every expected 404 is quiet.** New parametrized test
  `test_expected_404s_return_their_missing_value_without_an_error_log` covers
  `get_branch_head_time` → `None`, `list_claim_branches` → `[]`,
  `remove_issue_label` → `False`, `delete_branch` → `False`, `list_issues` → `[]`
  and `find_open_pr` → `None`. Each is called with minimal valid arguments while
  urlopen raises a 404, and none leaves a record at WARNING or above.
- [x] **An unexpected code still logs an error with GitHub's message.** New test
  `test_unexpected_status_still_logs_error_with_body`: urlopen raises 403 with
  body `{"message":"Resource not accessible by personal access token"}`.
  `get_repo_variable("o/r", "TICKET_ENGINE_PAUSED")` raises `HTTPError`, and one
  ERROR record contains both `403` and
  `Resource not accessible by personal access token`.
- [x] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are. No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- The Jules client's own `_request` in `src/ticket_engine/jules.py`.
- Logging in `live_dispatch.py` and `box_worker.py`.
- Any change to what a caller returns or raises.

## Comments

2026-10-06: `_request` takes `expected_codes` (DEBUG, re-raise); callers pass 422/404 as specified. New tests in `tests/test_github_adapter.py` cover criteria 1-5 (each seen failing first). Two existing assertions in `tests/test_escalation.py` (`delete_branch`, `set_repo_variable` PATCH) now include `expected_codes=(404,)`: unavoidable, as they assert the exact `_request` call args; nothing weakened. `tests/test_box_worker.py` has 2 failures that also fail on master (not this ticket).
