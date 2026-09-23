# 11: Bootstrap, GitHub side

**What to build:** After `adopt` (or `new`), the bootstrap configures the GitHub repo so it is protected and wired before the first dispatch.

**Blocked by:** 10

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] It reads `secrets.env` from a path outside any repo (default in the user's home) and sets `JULES_API_KEY`, `PIPELINE_TOKEN`, `PEOPLE_DENYLIST` as repo secrets; it never prints a value; the engine ships `secrets.env.example` with key names only.
- [ ] It enables auto-merge on the repo, secret scanning with push protection, and a ruleset on the default branch requiring the repo's own gates and the integrity check.
- [ ] It creates the `engine:hold`, `engine:escalated`, `engine:windows-waiting` labels.
- [ ] It prints the Jules environment setup script for the repo (Python version and system libraries from the repo config) and a checklist of manual steps it cannot automate.
- [ ] All GitHub operations go through an adapter that tests fake; tests assert the recorded operations; a second run performs no changes.

## Comments
