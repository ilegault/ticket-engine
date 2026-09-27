# 37: Done tickets' claims are released on every run, and the run report says so

**What to build:** When a ticket's PR merges, its file on the default branch reads `Status: done`, and the dispatcher should delete its claim branch `claim/<effort>/<NN>` on the next run. Today that release (step 4b of `LiveDispatcher.dispatch`) is skipped in three situations, so claims for finished tickets pile up and count against concurrency:

1. The repo is paused: the paused path returns before step 4b.
2. `jules_client.count_recent_sessions` fails: step 3 returns before step 4b.
3. Any live Jules session whose title contains `-<NN>:` keeps the claim, from **any repo and any effort**, and `PAUSED` / `AWAITING_USER_FEEDBACK` sessions count as live long after their PR merged.

After this ticket, the decision "which done claims to release" is a pure function in `dispatch.py`. It runs at the top of every live run, paused or not, before any Jules quota call. A session only protects a claim if it belongs to this repo and to this ticket's effort and number. Every run's report lists the done claims it released and the ones it kept, with the reason. The decision lives in a pure core because AGENTS.md §3 requires every decision to be testable from a static snapshot: the orchestrator only fetches and deletes.

Claims for tickets that are not `done` are untouched by this ticket. That is the stale-claim sweep's job (ticket 23).

**Blocked by:** 23, 24

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Core tests go in `tests/test_dispatch_scenarios.py`, using its `make_ticket` helper. Live tests go in `tests/test_live_dispatch.py`, using `MagicMock` GitHub and Jules clients the way `test_live_dispatch_releases_claim_for_done_ticket_with_no_live_session` does. Report tests go in `tests/test_run_report.py` and assert whole rendered lines. `DispatchCore`, the new core function and the parser are real; the GitHub and Jules clients are faked; no test reads the real clock. Write these tests first and watch them fail.

- [ ] **Pure core.** `dispatch.py` gains `release_done_claims(tickets, claim_refs, sessions, repo) -> tuple[list[ReleaseClaimAction], list[tuple[int, str, str]]]`. It does no I/O. `sessions` is `None` when the sessions could not be listed.
  - It parses each ref as `claim/<effort>/<NN>` and looks up the ticket with that `effort` **and** number. Refs that do not parse, or whose ticket is missing or not `is_done()`, appear in neither list.
  - A done ticket's claim is released (a `ReleaseClaimAction` with `reason="Ticket is done on the default branch"`) unless a session in `sessions` has `is_live_session_state(state)`, `_session_source_matches(sess, repo)` true, and a title matching `_SESSION_TITLE_RE` with the same effort and number. Then it is kept with reason `a live Jules session for this ticket is still open (<STATE>)`.
  - When `sessions is None`, every done claim is kept with reason `Jules sessions could not be listed, so the claim was not released blind`.
  - Tests, one each: released with no sessions; kept by a matching live session; **released** when the only live session is for another repo (`sourceContext.source` = `sources/github/other/repo`); **released** when the only live session has the same number but another effort; kept for every done claim when `sessions=None`; a claim for an `in-progress` ticket is in neither list.
- [ ] **Runs on every live run, paused or not.** `LiveDispatcher` gains a private method `_release_done_claims(tickets, facts) -> set[str]`. It calls `github_client.list_claim_branches`, then `jules_client.list_sessions` (passing `None` to the core if that raises), then `release_done_claims`, and calls `github_client.delete_branch(repo=..., branch=<claim_ref>)` for each release. It returns the set of refs it deleted.
  - `dispatch` calls it right after `facts` is built and **before** the `if self.paused:` block. Step 4b is removed.
  - Step 2's `existing_claims` then has the returned set subtracted, so a released claim frees its concurrency slot in the same run. Leave step 2's and step 4's own list calls as they are.
  - If `list_claim_branches` raises inside the new method, log it and return an empty set; dispatch continues exactly as before.
  - Tests: a paused dispatcher (`TICKET_ENGINE_PAUSED` read as `true` from the fake) with a done ticket and its claim calls `delete_branch` for that claim; a dispatcher whose `count_recent_sessions` raises `urllib.error.URLError` still calls `delete_branch` for a done ticket's claim.
- [ ] **Existing live tests still hold.** `test_live_dispatch_releases_claim_for_done_ticket_with_no_live_session` passes unchanged. `test_live_dispatch_does_not_release_claim_with_live_jules_session` is rewritten in place under the same name: its session dict gains `"sourceContext": {"source": "sources/github/owner/repo"}`, and all its assertions stay. No test is deleted.
- [ ] **Failures are recorded, not swallowed.** If `delete_branch` raises (`HTTPError`, `URLError`, `ValueError` or `OSError`), the claim goes into the kept list with reason `deleting the branch failed (HTTP <code>)` for an `HTTPError`, or `deleting the branch failed (<ExceptionClassName>)` otherwise. It is logged with `logger.error`. No response body reaches the reason text (ADR 0002). One test with an `HTTPError` whose code is 403.
- [ ] **Run report.** `RunFacts` gains `released_done_claims: list[tuple[int, str]]` (ticket number, claim ref) and `kept_done_claims: list[tuple[int, str, str]]` (ticket number, claim ref, reason), filled by `_release_done_claims`. `build_run_report` appends one note per entry, right after the `stopped_early` note and before the limit notes, on every run including paused ones:
  - released: `` 🧹 Ticket 16 is done; released its claim `claim/needs-you-waiting-on/16`. ``
  - kept: `` ⚠️ Ticket 16 is done but its claim `claim/needs-you-waiting-on/16` was kept: <reason>. ``
  - Tests assert both whole lines, and that a paused run's report contains the released line.
- [ ] **Docstrings.** `live_dispatch.py`'s `WHY THIS EXISTS` gains an item saying done claims are released at the top of every run, paused or not, and why (the three skip paths above left finished tickets holding concurrency slots). `release_done_claims` has a docstring naming this ticket and the repo-and-effort session match.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
