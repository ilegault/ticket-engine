# 11: Bootstrap, GitHub side

**What to build:** After `adopt` (or `new`), the bootstrap configures the GitHub repo so it is protected and wired before the first dispatch.

**Blocked by:** 10

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [x] It reads `secrets.env` from a path outside any repo (default in the user's home) and sets `JULES_API_KEY`, `PIPELINE_TOKEN`, `PEOPLE_DENYLIST` as repo secrets; it never prints a value; the engine ships `secrets.env.example` with key names only.
- [x] It enables auto-merge on the repo, secret scanning with push protection, and a ruleset on the default branch requiring the repo's own gates and the integrity check.
- [x] It creates the `engine:hold`, `engine:escalated`, `engine:windows-waiting` labels.
- [x] It prints the Jules environment setup script for the repo (Python version and system libraries from the repo config) and a checklist of manual steps it cannot automate.
- [x] All GitHub operations go through an adapter that tests fake; tests assert the recorded operations; a second run performs no changes.

## Comments
Progress (2026-09-22): all 5 criteria done, all gates green.
github_setup() pure core; SetSecretOp/CreateLabelOp/etc. typed ops; secrets.env.example shipped.
29 tests cover all criteria + idempotency. Adversarial mutation caught by 2 tests.
