# 36: Move v1 and confirm the box-first routing on Slackbot

**What to build:** The developer merges the held tickets, moves the engine's `v1` tag to include every ticket in this effort, turns on `box_enabled` in Slackbot, and confirms the new routing on a real Slackbot dispatch run.

**Blocked by:** 25, 26, 27, 33, 34, 35

**Status:** ready-for-developer

**Runner:** any

**Auto-merge:** no

**An agent must not claim this ticket.** Merging held PRs, moving a release tag every target repo pins, and editing a target repo's engine config are developer steps.

## Acceptance criteria

- [ ] Held tickets 33 and 34 merged by hand. `v1` moved to the merge commit that includes 19–34 (`git tag -f v1 <sha>`, then `git push -f origin v1`), and `git ls-remote --tags origin v1` shows that SHA.
- [ ] Slackbot's `.ticket-engine.toml` has `box_enabled = true`, and the box's local config lists Slackbot.
- [ ] A manually triggered Slackbot Dispatch run, while the box is idle, shows `**Box:** available (checked in …)` in its summary and starts no Jules session.
- [ ] With the box stopped for 12 hours or more (or its status issue body edited to an old check-in), a Dispatch run shows `**Box:** silent since …` and starts Jules on a `Runner: any` ticket. The `Box alert: box silent` issue opens within 3 hours and notifies the developer on GitHub Mobile.
- [ ] The next morning report shows a `**Box:**` line.

## Comments
