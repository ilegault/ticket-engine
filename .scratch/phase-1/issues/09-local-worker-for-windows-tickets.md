# 09: Local worker for Windows tickets

**What to build:** One command on the developer's machine picks up `Runner: windows` tickets across their repos and works them with the Antigravity CLI, with the same claims, gate and quota care as the cloud worker. In Phase 2 the same command runs as a scheduled task on the Windows mini PC.

**Blocked by:** 02, 06

**Status:** in-progress

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] A local config lists the developer's local clones; the command computes each repo's frontier restricted to `windows` tickets.
- [ ] It claims one ticket via the same claim-branch mechanism and works in a separate git worktree, never the developer's checkout.
- [ ] It drives `agy -p <assembled prompt> --output-format json`.
- [ ] Before starting, it reads remaining Antigravity quota from the local status endpoint when available, and refuses to start below 20%; if the endpoint is unavailable it proceeds and relies on quota-error detection.
- [ ] The skill instructs the worker to checkpoint at acceptance-criterion boundaries: commit and push WIP to the ticket branch plus a progress note of five lines or fewer under `## Comments`.
- [ ] On a quota error it keeps the claim, waits until the reported reset, then resumes with `agy --continue`; if that fails, a fresh session is started from the checkpoint and progress note.
- [ ] The resulting PR goes through the normal integrity gate.
- [ ] Tests use a fake `agy`: start, quota refusal, quota stop then resume, resume fallback to fresh session.

## Comments
