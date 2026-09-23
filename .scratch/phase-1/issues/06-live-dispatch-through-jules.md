# 06: Live dispatch through Jules

**What to build:** Pushing to a target repo's default branch (or the hourly timer) makes the engine claim frontier tickets and start Jules sessions on them, within quota, cap and concurrency limits.

**Blocked by:** 01, 02

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] A GitHub adapter creates a claim branch `claim/<effort>/<NN>` via the Git refs API; an already-exists response means "claimed" and the ticket is skipped.
- [ ] A Jules adapter creates a session with the assembled prompt, `sourceContext` for the repo and default branch, title `<effort>-<NN>: <ticket title>`, `automationMode: AUTO_CREATE_PR`, and no plan approval required; auth via `X-Goog-Api-Key`.
- [ ] Quota: the dispatcher lists Jules sessions and counts those created in the last 24 hours; it never starts one when fewer than the configured reserve (default 10 of 100) remain.
- [ ] Daily cap per repo and concurrency (default 2 in flight per repo) come from the repo config and are enforced.
- [ ] A reusable dispatch workflow runs the dispatcher on push to the default branch, hourly, and manually, using `PIPELINE_TOKEN`.
- [ ] A paused repo (`TICKET_ENGINE_PAUSED` set) starts nothing.
- [ ] Adapters are faked in core tests; each adapter has a contract test against recorded API responses; no test calls a live API.
- [ ] Scenario tests: quota at the reserve boundary, cap reached, claim collision, paused repo.

## Comments
