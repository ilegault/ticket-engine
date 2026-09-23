# 06: Live dispatch through Jules

**What to build:** Pushing to a target repo's default branch (or the hourly timer) makes the engine claim frontier tickets and start Jules sessions on them, within quota, cap and concurrency limits.

**Blocked by:** 01, 02

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [x] A GitHub adapter creates a claim branch `claim/<effort>/<NN>` via the Git refs API; an already-exists response means "claimed" and the ticket is skipped.
- [x] A Jules adapter creates a session with the assembled prompt, `sourceContext` for the repo and default branch, title `<effort>-<NN>: <ticket title>`, `automationMode: AUTO_CREATE_PR`, and no plan approval required; auth via `X-Goog-Api-Key`.
- [x] Quota: the dispatcher lists Jules sessions and counts those created in the last 24 hours; it never starts one when fewer than the configured reserve (default 10 of 100) remain.
- [x] Daily cap per repo and concurrency (default 2 in flight per repo) come from the repo config and are enforced.
- [x] A reusable dispatch workflow runs the dispatcher on push to the default branch, hourly, and manually, using `PIPELINE_TOKEN`.
- [x] A paused repo (`TICKET_ENGINE_PAUSED` set) starts nothing.
- [x] Adapters are faked in core tests; each adapter has a contract test against recorded API responses; no test calls a live API.
- [x] Scenario tests: quota at the reserve boundary, cap reached, claim collision, paused repo.

## Comments

### Implementation & Verification Summary (2026-09-22)
- Built `GitHubClient` (`src/ticket_engine/github.py`): creates claims via Git refs API (`POST /repos/{repo}/git/refs`), treats 422 ("Reference already exists") as "claimed" (skips ticket). Tested against recorded JSON fixtures in `tests/test_github_adapter.py`.
- Built `JulesClient` (`src/ticket_engine/jules.py`): starts sessions with assembled prompt, `sourceContext`, `<effort>-<NN>: <ticket title>` title, `AUTO_CREATE_PR`, `requirePlanApproval=False`, authenticated via `X-Goog-Api-Key`. Lists sessions and counts rolling 24h creation. Tested against recorded JSON fixtures in `tests/test_jules_adapter.py`.
- Built `RepoConfig` (`src/ticket_engine/config.py`): loads configuration via `tomllib` (`concurrency`, `daily_cap`, `jules_reserve`, `jules_limit`).
- Updated `DispatchCore` (`src/ticket_engine/dispatch.py`): enforces quota reserve ("The reserve stops a start at 91 sessions"), daily cap per repo, concurrency limits, and skips already-claimed tickets.
- Built `LiveDispatcher` (`src/ticket_engine/live_dispatch.py`) and updated `cli.py`: coordinates GitHub and Jules adapters with pure prompt assembly without printing or logging secrets/prompts. Tested in `tests/test_live_dispatch.py` and `tests/test_cli.py`.
- Created reusable dispatch workflow `.github/workflows/dispatch.yml` triggered on push to default branch, hourly schedule, and manual dispatch.
- All gates passing: `ruff check .` clean, `check_tests_first.py` clean, `pytest` 46/46 passed. Adversarial self-review confirmed mutation on quota check fails test as expected.
