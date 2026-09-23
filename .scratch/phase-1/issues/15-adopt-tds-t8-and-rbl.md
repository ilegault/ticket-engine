# 15: Adopt TDS-T8 and RBL

**What to build:** The two Windows-CI repos are wired to the engine, with any work Jules cannot verify on Linux routed to the local worker.

**Blocked by:** 14

**Status:** ready-for-developer

**Runner:** any

**Auto-merge:** no

**An agent must not claim this ticket.** It needs the developer's secrets, GitHub settings and the Jules web UI.

## Acceptance criteria

- [ ] Bootstrap `adopt` run on TDS-T8 and on RBL; PRs reviewed and merged by the developer.
- [ ] For each: whether the suite passes in Jules's Linux VM is recorded; if not, the repo config marks all its tickets `windows` until Phase 2.
- [ ] Jules setup scripts pasted and snapshotted; GitHub side run for both.
- [ ] RBL's AGENTS.md §11 CI-watching step checked to be consistent with the engine skill (developer edits AGENTS.md; the bootstrap does not).

## Comments
