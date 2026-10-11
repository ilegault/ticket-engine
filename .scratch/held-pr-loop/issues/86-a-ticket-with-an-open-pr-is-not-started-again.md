# 86: A ticket that already has an open PR is never started again

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None

**Binding:** ADR 0001; ADR 0004

## What to build

Live dispatch decides what to start from the ticket files on the default branch and
the claim branches. It never looks at open pull requests: `LiveDispatcher.dispatch`
(`src/ticket_engine/live_dispatch.py`) builds its step-5 `WorldSnapshot` without
`open_prs`. A held PR leaves its ticket `ready-for-agent` on the default branch, so
once the claim branch goes away the same ticket is started again. On TDS-T8 one
ticket was started ten times in two days, each run opening another held PR.

After this ticket a ticket with an open PR is skipped, whatever the PR's labels.
Its PR does not take a concurrency slot (a held PR waits for the developer; other
tickets keep moving), so the information travels in a new field and `open_prs`,
which drives escalation and slot counting, is left alone.

1. `WorldSnapshot` (`src/ticket_engine/dispatch.py`) gains
   `tickets_with_open_prs: frozenset[int] = frozenset()`.
2. `DispatchCore.evaluate`, in the frontier loop under `# 7. Check available slots`:
   right after the `is_ticket_claimed(...)` branch, a ticket whose number is in
   `snapshot.tickets_with_open_prs`, or is the `ticket_number` of any `OpenPR` in
   `snapshot.open_prs`, is skipped with `continue`. `in_flight_count` is unchanged.
3. A new pure function in `src/ticket_engine/dispatch.py`:
   `ticket_numbers_with_open_prs(head_refs: Iterable[str], tickets: Sequence[Ticket]) -> frozenset[int]`.
   A ticket matches when a head ref starts with
   `f"ticket/{ticket.effort or 'phase-1'}-{ticket.number:02d}-"`. Prefix, not equality,
   because Jules appends a session id to the slug
   (`ticket/cold-start-and-traceability-20-simulated-filament-13276867537050285691`).
4. `GitHubClient.list_open_pr_head_refs(repo: str) -> list[str]`
   (`src/ticket_engine/github.py`): `GET /repos/{repo}/pulls?state=open&per_page=100`,
   returning each item's `["head"]["ref"]`. Copy the request and error style of
   `list_claim_branches`.
5. `LiveDispatcher.dispatch`: right after the claim branches are listed (step 2),
   call `list_open_pr_head_refs(self.repo)`. On `HTTPError`, `URLError`,
   `ValueError` or `OSError`: log a warning, set
   `facts.stopped_early = f"could not list open pull requests ({exc})"` and return
   `[]`, because starting blind could duplicate a ticket. Otherwise pass
   `tickets_with_open_prs=ticket_numbers_with_open_prs(refs, tickets)` into the
   step-5 `WorldSnapshot`.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Core tests build a
`WorldSnapshot` by hand, as `test_scenario_claim_collision_skips_claimed_ticket` in
`tests/test_dispatch_scenarios.py` does. Live-dispatch tests fake the GitHub and
Jules clients with `MagicMock`, as `test_live_dispatch_claims_and_starts_jules_session`
in `tests/test_live_dispatch.py` does; the dispatcher and core are real.

- [ ] **The core skips it and starts the next ticket.** Test `test_scenario_ticket_with_an_open_pr_is_not_started` in `tests/test_dispatch_scenarios.py`: tickets 1 and 2, no claims, `concurrency=1`, `tickets_with_open_prs=frozenset({1})`; asserts exactly one `StartTicketAction`, for ticket 2.
- [ ] **Jules-suffixed branch names match, near-misses do not.** Test `test_ticket_numbers_with_open_prs_matches_suffixed_ticket_branches` in `tests/test_dispatch_scenarios.py`: tickets 19 and 20 in effort `cold-start-and-traceability`; refs `ticket/cold-start-and-traceability-20-simulated-filament-13276867537050285691`, `ticket/cold-start-and-traceability-200-other` and `claim/cold-start-and-traceability/19`; asserts the result is `frozenset({20})`.
- [ ] **Live dispatch does not restart it.** Test `test_live_dispatch_does_not_restart_a_ticket_with_an_open_pr` in `tests/test_live_dispatch.py`: `list_open_pr_head_refs` returns `["ticket/phase-1-01-test-ticket-1-123456"]`; tickets 1 and 2; asserts `create_session` was called once, with title starting `phase-1-02`.
- [ ] **No PR list, no starts.** Test `test_live_dispatch_starts_nothing_when_open_prs_cannot_be_listed` in `tests/test_live_dispatch.py`: `list_open_pr_head_refs` raises `urllib.error.URLError("down")`; asserts `dispatch` returns `[]`, `create_session` was not called, and `dispatcher.last_run.stopped_early` contains `could not list open pull requests`.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
