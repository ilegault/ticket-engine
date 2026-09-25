# 18: Move v1 and confirm the new column on Slackbot

**What to build:** The developer moves the engine's `v1` tag to the commit that includes tickets 16 and 17, so target repos pick up the new report. Then the developer confirms it on a real Slackbot dispatch run.

**Blocked by:** 16, 17

**Status:** ready-for-developer

**Runner:** any

**Auto-merge:** no

**An agent must not claim this ticket.** Moving a release tag that every target repo pins, and checking a live Actions run, are developer steps.

## Acceptance criteria

- [ ] `v1` moved to the merge commit that includes 16 and 17 (`git tag -f v1 <sha>` then `git push -f origin v1`), and `git ls-remote --tags origin v1` shows that sha.
- [ ] A manually triggered Slackbot Dispatch run's summary page shows the Needs-you header `| Ticket | Title | Waiting on | Holding up |`.
- [ ] In that run, the row for Slackbot's template-deploy developer ticket lists its unfinished blockers with statuses (or `ready now` if they have all landed by then).
- [ ] The next morning-report issue shows a `**Needs you (ready-for-developer):**` line under the Slackbot section.

## Comments
