# 23: Stale claims use real progress times, and box claims go stale at 8 hours

**What to build:** The dispatcher releases stale claims on every live run, and the rule finally has data to work with.
- For every claim branch, the live dispatcher reads who holds it (`Claimed-by` on the claim branch's ticket file) and when it last made progress (the newest commit on the ticket branch `ticket/<effort>-<NN>-<slug>` if that branch exists, otherwise the claim branch head).
- A box claim with no progress for `box_stale_claim_hours` (default 8) is released, whether or not anything looks live.
- A Jules claim keeps the 12-hour rule and its live-session exception.
- A claim whose progress time could not be read is never released.

Today `LiveDispatcher.dispatch` passes claims as bare strings, so no claim is ever stale. Spec: `.scratch/box-primary-worker/spec.md` (§Dispatcher changes). ADR 0006 rule 5.

**Blocked by:** 21

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Core tests go in `tests/test_dispatch_scenarios.py`, using its `make_ticket` helper. Live tests go in `tests/test_live_dispatch.py`, using its fake GitHub and Jules clients. `DispatchCore` and the parser are real, and the clock is `WorldSnapshot.now`. Keep the field name `Claim.last_commit_time`; its meaning widens to "last progress time", and its comment says so. Write these tests first and watch them fail.

- [x] **Config and claim.**
  - `RepoConfig` gains `box_stale_claim_hours: int = 8`, loaded from `.ticket-engine.toml` by `load_repo_config` like `stale_claim_hours`.
  - `Claim` gains `claimed_by: str = ""`, where `""` means a pre-Phase-2 claim and is treated as `jules`.
  - A test loads `box_stale_claim_hours = 6` from a temp TOML file.
- [x] **Core rule.** In `DispatchCore.evaluate` step 1:
  - A `Claim(claimed_by="box", last_commit_time=now-8h)` gives a `ReleaseClaimAction` whose reason text is `Stale box claim: no checkpoint in 8 hours`, even when `snapshot.jules_sessions` holds a live session whose title matches the ticket.
  - At `now-7h59m` there is no release.
  - A Jules claim at 13 hours with a live session is kept, and without one it is released. This is the existing `test_scenario_stale_claim_13_hour_quiet_released_and_11_hour_quiet_kept`, which must pass unchanged.
  - A claim with `last_commit_time=None` is never released.
- [x] **Adapter.** `GitHubClient.get_branch_head_time(repo, branch) -> datetime | None` reads `GET /repos/{repo}/branches/{branch}` and returns the head commit's `commit.committer.date`, or `None` on 404. Test it with a recorded response added to `tests/fixtures/github/`, the way `tests/test_github_adapter.py` tests `create_claim_branch`.
- [x] **Live wiring.** `LiveDispatcher.dispatch` builds a `Claim` for every claim branch left after the done-ticket release in step 4b.
  - It fills `claimed_by` from the claim branch's ticket file, parsed with `TicketParser`.
  - It fills `last_commit_time` from `get_branch_head_time` on the ticket branch, falling back to the claim branch.
  - It passes these `Claim`s into the snapshot, then carries out every `ReleaseClaimAction` with `delete_branch`, exactly as `dispatch_escalations_and_stale_claims` does.
  - A read failure leaves `last_commit_time=None` and is logged. A test with a fake whose ticket branch head is 9 hours old and `Claimed-by: box` asserts the claim branch was deleted. A second test with a head 1 hour old asserts it was not.
- [x] **Report and docstring.** Released claims appear in `RunFacts` in a new field `released_stale: list[tuple[int, str]]` (ticket number, reason). `build_run_report` renders a line `**Released stale claims:**` followed by `- <NN> — <reason>` per claim. The test asserts the whole line. `dispatch.py`'s and `live_dispatch.py`'s docstrings say the sweep now runs live, and cite ADR 0006.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

Completed 2026-09-26:
- Added `box_stale_claim_hours: int = 8` to `RepoConfig` and `load_repo_config`.
- Extended `Claim` with `claimed_by: str = ""` (treating empty string as jules) and updated comment for `last_commit_time` to reflect last progress time.
- Implemented `DispatchCore.evaluate` rule for box claims (released at `box_stale_claim_hours` without live session check) and Jules claims (retaining 12h rule and live session exception). Claims with `None` progress time are never released.
- Added `GitHubClient.get_branch_head_time` to query commit committer date and return None on 404.
- Wired live stale claims evaluation in `LiveDispatcher.dispatch`, building `Claim` objects with `claimed_by` from claim branch and `last_commit_time` from ticket/claim branches, executing `ReleaseClaimAction` via `delete_branch`.
- Added `released_stale` field to `RunFacts` and rendered `**Released stale claims:**` in `build_run_report`.
- Updated module docstrings in `dispatch.py` and `live_dispatch.py` citing ADR 0006.
- Tests added in `tests/test_dispatch_scenarios.py`, `tests/test_github_adapter.py`, `tests/test_live_dispatch.py`, and `tests/test_run_report.py`.

