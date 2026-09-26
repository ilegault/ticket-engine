# 24: The dispatcher leaves the frontier to the box unless the box cannot take it

**What to build:** Each live dispatch run reads the box status issue from the engine repo and parses it with `box_status.parse_box_status`.
- While the box is available (state `working` or `idle`, checked in less than `box_silent_hours` ago), the dispatcher starts no Jules session and reports the tickets as left for the box.
- While the box is paused, silent, or the issue is missing or unreadable, `Runner: any` tickets go to Jules as before (overflow).
- `Runner: windows` tickets are never started by the dispatcher.

The run report shows the box's state on every run. Spec: `.scratch/box-primary-worker/spec.md` (§Dispatcher changes, Overflow gate). ADR 0006 rule 3.

**Blocked by:** 20

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Core tests go in `tests/test_dispatch_scenarios.py` (`make_ticket` helper), report tests in `tests/test_run_report.py` (whole-line assertions), and live tests in `tests/test_live_dispatch.py` (fake clients). `DispatchCore`, `box_status` and the parser are real. Existing tests that build a `WorldSnapshot` without `box` must keep passing, because they model a Phase 1 world. So `box` defaults to a sentinel meaning "no box configured", which keeps today's behaviour. Write these tests first and watch them fail.

- [ ] **Snapshot and config.**
  - `WorldSnapshot` gains `box: BoxStatus | None | NoBox = NO_BOX` and `box_status_error: str = ""`. `NO_BOX` is a module-level sentinel meaning the repo has no box configured, so behaviour is unchanged.
  - `RepoConfig` gains `box_silent_hours: int = 12` and `box_enabled: bool = False`, both loaded by `load_repo_config`.
  - `DispatchResult` gains `left_for_box: list[Ticket]` and `box_state: str`, one of `none`, `available`, `paused`, `silent`, `unreadable`.
- [ ] **The gate.** With `box` set to a `BoxStatus` checked in 1 hour ago and in state `idle`, `evaluate` returns no `StartTicketAction`, `box_state == "available"`, and `left_for_box` equal to the unclaimed, lint-clean frontier.
  - The same snapshot with state `paused_quota` or `paused_weekly_cap` gives `paused`, and `login_expired` also gives `paused`. A check-in 12 hours or more old gives `silent`. `box=None` gives `unreadable`.
  - In all four of those cases, `Runner: any` tickets get a `StartTicketAction` under the existing limits.
  - A `Runner: windows` ticket is never started, in any state.
  - One test per state.
- [ ] **Live read.** When `config.box_enabled`, `LiveDispatcher.dispatch` finds the open issue labelled `engine:box-status` in the engine repo (constant `ENGINE_REPO = "ilegault/ticket-engine"` in `live_dispatch.py`, the same repo `scripts/run_morning_report.py` writes to) through the GitHub REST issues list. It passes `parse_box_status(body)` as `box`.
  - A failed read sets `box=None` and puts the error text into `box_status_error`. It never raises.
  - When `box_enabled` is false, it passes `NO_BOX` and makes no request. The test asserts the fake recorded no issues request.
- [ ] **Run report.** `RunFacts` gains `box_state`, `box_checked_in: datetime | None`, `left_for_box: list[Ticket]`, and `box_status_error`. For `box_state` other than `none`, `build_run_report` renders one line:
  - `available`: `**Box:** available (checked in 03:12 UTC). Left for the box: 21, 22.`
  - `paused`: `**Box:** paused — overflow to Jules is on.`
  - `silent`: `**Box:** silent since 03:12 UTC — overflow to Jules is on.`
  - `unreadable`: `**Box:** status unreadable — overflow to Jules is on.`

  Ticket numbers are zero-padded through `_label`. When the box is available and nothing started, the existing `**Started 0 tickets.**` line is followed by that Box line, so "started 0" carries its reason (`AGENTS.md` §4). Assert each line whole.
- [ ] **Docstrings.** `dispatch.py`'s `WHY THIS EXISTS` gains an `OVERFLOW (ADR 0006)` section that explains why an unreadable status fails toward overflow. Inverting the `available` test turns at least two tests red; check this by hand once before landing.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
