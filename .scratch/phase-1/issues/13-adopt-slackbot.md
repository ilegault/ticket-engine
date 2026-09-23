# 13: Adopt Slackbot for real

**What to build:** Slackbot is wired to the engine and ready for its first overnight run.

**Blocked by:** 08, 11

**Status:** ready-for-developer

**Runner:** any

**Auto-merge:** no

**An agent must not claim this ticket.** It needs the developer's secrets, GitHub settings and the Jules web UI.

## Acceptance criteria

- [ ] Engine tagged `v1`.
- [ ] Bootstrap `adopt` run on Slackbot; its PR reviewed and merged by the developer.
- [ ] The real Slack ID in the lifecycle-and-buyers ticket replaced with a role.
- [ ] Jules environment setup script pasted into Jules for Slackbot and snapshotted; Slackbot's suite passes in Jules's VM.
- [ ] Bootstrap GitHub side run; secrets, ruleset, auto-merge and labels confirmed in GitHub settings.

## Comments
