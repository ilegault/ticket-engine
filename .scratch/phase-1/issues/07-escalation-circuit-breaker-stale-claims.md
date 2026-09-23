# 07: Escalation, circuit breaker and stale claims

**What to build:** When a ticket cannot be made to pass honestly, it stops consuming quota and lands in front of the developer with a ready-to-use brief; repeated failure pauses the repo; dead claims free themselves.

**Blocked by:** 06

**Status:** in-progress

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] The dispatcher counts failed CI runs across a PR's successive head commits; after the third it escalates.
- [ ] Escalating commits the ticket's `Status: blocked` and the escalation brief under `## Comments` to the PR branch, converts the PR to draft, and applies `engine:escalated`.
- [ ] The brief is assembled from the ticket, the Jules session's activities and the failing CI log excerpt, in the format the ticket skill defines.
- [ ] Two escalations in one repo within 24 hours set `TICKET_ENGINE_PAUSED`; clearing it resumes dispatch on the next run.
- [ ] A claim with no live Jules session and no ticket-branch commit newer than 12 hours is released (branch deleted) and its ticket returns to the frontier.
- [ ] Tickets whose blocker is escalated or held stay off the frontier; independent tickets keep being dispatched.
- [ ] Scenario tests cover: second vs third failure, two escalations in and outside 24 hours, a 13-hour-quiet claim, an 11-hour-quiet claim.

## Comments
