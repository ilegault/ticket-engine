# 26: Escalations open an issue that notifies the developer

**What to build:** Every escalation the dispatcher carries out opens one issue in the ticket's target repo. That covers PR escalation after the fix attempts run out (`EscalatePRAction`) and a Jules session that kept asking (`EscalateWaitingSessionAction`). The issue is labelled `escalation`, @mentions the repo owner, links the PR or claim branch, and gives a fixed reason. GitHub Mobile then notifies the developer, never email. At most one issue is open per ticket. Each run closes an open escalation issue once its ticket is `done` on the default branch or its claim branch is gone. The issue text comes only from `box_status.render_escalation_issue`, and the escalation brief stays in the ticket file. Spec: `.scratch/box-primary-worker/spec.md` (§Escalation issues). CONTEXT *Escalation*.

**Blocked by:** 20

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Adapter tests go in `tests/test_github_adapter.py`, with recorded responses in `tests/fixtures/github/`. Live tests go in `tests/test_escalation.py` and `tests/test_waiting_sessions.py`, using the fakes those files already use. `box_status` and `DispatchCore` are real. Write these tests first and watch them fail.

- [x] **Adapter.** `GitHubClient` gains:
  - `create_issue(repo, title, body, labels) -> int`, via REST `POST /repos/{repo}/issues`;
  - `find_open_issue(repo, label, title) -> int | None`, via `GET /repos/{repo}/issues?state=open&labels=<label>`, exact title match;
  - `close_issue(repo, number)`, via `PATCH` with `state: closed`.

  Copy `add_issue_labels` for request shape and error handling. There is one recorded-response test per method.
- [x] **Opened on PR escalation.** When `dispatch_escalations_and_stale_claims` carries out an `EscalatePRAction`, and after the escalation commit, it calls `find_open_issue(repo, "escalation", title)` and, if that returns `None`, `create_issue`.
  - The title and body come from `render_escalation_issue` with reason `ci_failed` and link `https://github.com/<repo>/pull/<NN>`.
  - `owner` is `repo.split("/")[0]`, never a literal.
  - The test asserts the fake's recorded title `Escalation: <effort>-<NN> <slug>`, a body starting `@<owner>`, and labels `["escalation"]`.
  - A second run with the issue already open creates nothing.
- [x] **Opened on waiting-session escalation.** `_handle_waiting_sessions` does the same after the escalation commit to the claim branch. It uses reason `kept_asking` and link `https://github.com/<repo>/tree/<claim_ref>`.
- [x] **Closed when resolved.** Each `dispatch` run lists open `escalation` issues (new `list_open_issues(repo, label)`). It closes each one whose ticket number (parsed from the title) is `is_done()` on the default branch, or whose `claim/<effort>/<NN>` branch is not in `list_claim_branches`. A test covers both close paths and one open ticket that is left alone.
- [x] **No free text reaches the issue.** A test escalates a PR whose fake `ci_log_excerpt` is `"SECRET-TOKEN-abc"` and asserts that string is absent from every recorded issue title and body. A failed `create_issue` is logged and recorded in `RunFacts.session_failures` style (new `issue_failures: list[tuple[int, str]]`), never raised.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

**2026-09-26 — done.** Built exactly per the acceptance criteria:

- `GitHubClient` (`src/ticket_engine/github.py`) gains `create_issue`, `find_open_issue`,
  `close_issue`, `list_open_issues`, the latter two built on the existing `list_issues`.
  One recorded-response test per method in `tests/test_github_adapter.py`, with new
  fixtures under `tests/fixtures/github/` (`issue_create_success.json`,
  `issues_open_escalation_list.json`, `issue_close_success.json`).
- `box_status.py` gains `parse_escalation_issue_title`, the one place that reads a
  `render_escalation_issue` title back (effort, ticket number, slug), tested in
  `tests/test_box_status.py`.
- `LiveDispatcher._open_escalation_issue` (new helper) is called from
  `dispatch_escalations_and_stale_claims`'s `EscalatePRAction` branch (reason
  `ci_failed`, link to the PR) and from `_handle_waiting_sessions`'s
  `EscalateWaitingSessionAction` branch (reason `kept_asking`, link to the claim
  branch tree), both after their escalation commit. It always checks
  `find_open_issue` first, so at most one issue stays open per ticket, and a
  failed `create_issue`/`find_open_issue` call is logged and recorded in the new
  `RunFacts.issue_failures`, never raised. Covered in `tests/test_escalation.py`
  and `tests/test_waiting_sessions.py`, including a test that a fake
  `ci_log_excerpt` of `"SECRET-TOKEN-abc"` never reaches any recorded issue
  title/body/labels.
- `LiveDispatcher._close_resolved_escalations` (new helper), called once per
  `dispatch()` run, lists open `escalation` issues, parses each title, and closes
  it when its ticket `is_done()` on the default branch or its claim branch is no
  longer in `list_claim_branches`. Covered by three tests in
  `tests/test_escalation.py`: closes on ticket-done, closes on claim-gone, leaves
  an unresolved one open.

**Pre-existing bug fixed in passing:** the merge that landed ticket 25 on
`master` (PR #32) dropped the box-checkpoint-detection block in
`LiveDispatcher.dispatch` (§4e of commit `919964b`) while keeping its
`checkpoints=checkpoints` use in the `WorldSnapshot` call below it. Every call to
`dispatch()` on `master` currently raises `NameError: name 'checkpoints' is not
defined` (26 tests failing before this branch). This ticket needs `dispatch()`
working to test escalation-issue closing, so the dropped block is restored here
verbatim from `919964b`, along with the missing test imports (`NO_BOX` in
`tests/test_dispatch_scenarios.py`; `BoxState`/`BoxStatus`/`render_box_status` in
`tests/test_live_dispatch.py`; `datetime` in `tests/test_run_report.py`) and the
duplicate `box_state`/`box_checked_in`/`left_for_box`/`box_status_error` fields
the same merge left in `RunFacts` (`ruff` `PIE794`). None of this changes any
tested behaviour; it only restores what a merge already meant to land.

Gates: `ruff check .` clean, `python scripts/check_tests_first.py` OK, `pytest -q`
465 passed. Adversarial self-review: reverted the `find_open_issue is None` guard
and the claim-gone check in turn, watched
`test_pr_escalation_creates_nothing_when_an_issue_is_already_open` and
`test_dispatch_closes_escalation_issue_once_its_claim_branch_is_gone` go red, then
restored both.
