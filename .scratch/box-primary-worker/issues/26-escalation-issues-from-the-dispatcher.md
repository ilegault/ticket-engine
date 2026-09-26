# 26: Escalations open an issue that notifies the developer

**What to build:** Every escalation the dispatcher carries out opens one issue in the ticket's target repo. That covers PR escalation after the fix attempts run out (`EscalatePRAction`) and a Jules session that kept asking (`EscalateWaitingSessionAction`). The issue is labelled `escalation`, @mentions the repo owner, links the PR or claim branch, and gives a fixed reason. GitHub Mobile then notifies the developer, never email. At most one issue is open per ticket. Each run closes an open escalation issue once its ticket is `done` on the default branch or its claim branch is gone. The issue text comes only from `box_status.render_escalation_issue`, and the escalation brief stays in the ticket file. Spec: `.scratch/box-primary-worker/spec.md` (§Escalation issues). CONTEXT *Escalation*.

**Blocked by:** 20

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Adapter tests go in `tests/test_github_adapter.py`, with recorded responses in `tests/fixtures/github/`. Live tests go in `tests/test_escalation.py` and `tests/test_waiting_sessions.py`, using the fakes those files already use. `box_status` and `DispatchCore` are real. Write these tests first and watch them fail.

- [ ] **Adapter.** `GitHubClient` gains:
  - `create_issue(repo, title, body, labels) -> int`, via REST `POST /repos/{repo}/issues`;
  - `find_open_issue(repo, label, title) -> int | None`, via `GET /repos/{repo}/issues?state=open&labels=<label>`, exact title match;
  - `close_issue(repo, number)`, via `PATCH` with `state: closed`.

  Copy `add_issue_labels` for request shape and error handling. There is one recorded-response test per method.
- [ ] **Opened on PR escalation.** When `dispatch_escalations_and_stale_claims` carries out an `EscalatePRAction`, and after the escalation commit, it calls `find_open_issue(repo, "escalation", title)` and, if that returns `None`, `create_issue`.
  - The title and body come from `render_escalation_issue` with reason `ci_failed` and link `https://github.com/<repo>/pull/<NN>`.
  - `owner` is `repo.split("/")[0]`, never a literal.
  - The test asserts the fake's recorded title `Escalation: <effort>-<NN> <slug>`, a body starting `@<owner>`, and labels `["escalation"]`.
  - A second run with the issue already open creates nothing.
- [ ] **Opened on waiting-session escalation.** `_handle_waiting_sessions` does the same after the escalation commit to the claim branch. It uses reason `kept_asking` and link `https://github.com/<repo>/tree/<claim_ref>`.
- [ ] **Closed when resolved.** Each `dispatch` run lists open `escalation` issues (new `list_open_issues(repo, label)`). It closes each one whose ticket number (parsed from the title) is `is_done()` on the default branch, or whose `claim/<effort>/<NN>` branch is not in `list_claim_branches`. A test covers both close paths and one open ticket that is left alone.
- [ ] **No free text reaches the issue.** A test escalates a PR whose fake `ci_log_excerpt` is `"SECRET-TOKEN-abc"` and asserts that string is absent from every recorded issue title and body. A failed `create_issue` is logged and recorded in `RunFacts.session_failures` style (new `issue_failures: list[tuple[int, str]]`), never raised.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
