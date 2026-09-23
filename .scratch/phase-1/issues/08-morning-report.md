# 08: Morning report

**What to build:** Every morning the developer gets one place that says what happened overnight and what needs them.

**Blocked by:** 07

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [x] A scheduled workflow in the engine repo runs daily at 12:00 UTC (7 am Central) and on demand.
- [x] It rewrites a single pinned issue in the engine repo (creating it once) listing, per target repo: merged, escalated (with links to briefs), held (with reasons), `windows`-waiting, paused, parse findings, and Jules quota standing.
- [x] Target repos to report on come from an engine-level list in the engine repo.
- [x] Rendering is a pure function of the same world snapshot the dispatcher uses; tests assert on the rendered text for sample snapshots.
- [x] Nothing in the report names a person or prints a secret.

## Comments

Built `morning_report.py` (pure renderer), extended `WorldSnapshot` with `repo_name`/`merged_prs`,
added `.github/workflows/morning-report.yml` (cron 0 12 * * *, workflow_dispatch), `engine-repos.toml`
(engine-level target-repo list), and `scripts/run_morning_report.py` (I/O adapter for the workflow).
18 tests in `test_morning_report.py` cover all five ACs; each test drives `render_morning_report()`
from a static snapshot. Broken-impl check confirms tests catch a bad label constant.
Note: `test_local_worker.py` is an untracked ticket-09 artifact on this machine; it won't appear in CI.
