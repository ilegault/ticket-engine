# 33: Scheduled check alerts the developer when the box goes silent

**What to build:** A scheduled workflow in the engine repo runs every 3 hours. It reads the box status issue and parses it with `parse_box_status`. When the box has not checked in for `box_silent_hours` (12), or the body is unreadable, and no `Box alert: box silent` issue is open, it opens one that @mentions the repo owner. When the box has checked in again and that alert is open, it closes it. The logic is a pure function plus a small script, and the workflow only calls the script. Spec: `.scratch/box-primary-worker/spec.md` (§Held changes). CONTEXT *Box silent*. ADR 0007 rule 4.

**Blocked by:** 20, 26

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

This ticket changes `.github/`, so the integrity gate always holds it (ADR 0001 check 4). It lands only when the developer merges it by hand. That is intended.

## Acceptance criteria

Tests go in a new `tests/test_box_silent_check.py`. `box_status` and the decision function are real. The script's GitHub calls go through an injectable client, faked. Write these tests first and watch them fail.

- [ ] **Pure decision.** `box_status.silent_check_action(status: BoxStatus | None, now, silent_hours, alert_open: bool) -> "open" | "close" | "none"`. It returns `open` when (`status is None`, or `now - status.checked_in_at >= silent_hours`) and `not alert_open`; `close` when not silent and `alert_open`; `none` otherwise. There are four tests, including 11 h 59 m (not silent) and 12 h 00 m (silent).
- [ ] **Script.** `scripts/check_box_silent.py` has `main(client, repo, now) -> int`. It finds the `engine:box-status` issue, parses it, finds an open `Box alert: box silent` issue labelled `engine:box-alert`, and carries out the action with `create_issue` (title and body from `render_box_alert(AlertKind.box_silent, owner, since)`, where `since` is the last check-in or `now`) or `close_issue`. `owner` is `repo.split("/")[0]`. A test per action asserts the fake's recorded calls.
- [ ] **Workflow.** `.github/workflows/box-silent-check.yml`:
  - triggers `schedule: - cron: '41 */3 * * *'` and `workflow_dispatch`;
  - has permissions `issues: write` and `contents: read`;
  - checks out, sets up Python 3.12, runs `pip install -e .`, then `python scripts/check_box_silent.py` with `GITHUB_TOKEN` and `GITHUB_REPOSITORY` from the context.

  A test loads the YAML as text and asserts the cron line, the permissions block and the script invocation are present.
- [ ] **Public text only.** The script prints nothing but one status line from a fixed set (`box silent alert opened`, `box silent alert closed`, `no change`). A test captures stdout for each action and asserts it equals one of those.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
