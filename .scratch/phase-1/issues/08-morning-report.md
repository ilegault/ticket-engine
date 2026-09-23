# 08: Morning report

**What to build:** Every morning the developer gets one place that says what happened overnight and what needs them.

**Blocked by:** 07

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] A scheduled workflow in the engine repo runs daily at 12:00 UTC (7 am Central) and on demand.
- [ ] It rewrites a single pinned issue in the engine repo (creating it once) listing, per target repo: merged, escalated (with links to briefs), held (with reasons), `windows`-waiting, paused, parse findings, and Jules quota standing.
- [ ] Target repos to report on come from an engine-level list in the engine repo.
- [ ] Rendering is a pure function of the same world snapshot the dispatcher uses; tests assert on the rendered text for sample snapshots.
- [ ] Nothing in the report names a person or prints a secret.

## Comments
