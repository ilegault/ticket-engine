# 04: Integrity gate, hold rules and the denylist

**What to build:** The gate knows when a green PR must still wait for the developer, and it stops real names and IDs from reaching a public repo.

**Blocked by:** 03

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] Check 3: any ratchet file (paths from the repo config) with a higher value than on base → `fail`.
- [ ] Check 4: a PR touching `.github/`, the gate scripts, `docs/adr/`, `AGENTS.md` or `CONTEXT.md` → `hold`, naming the paths.
- [ ] Check 5: a tests-first escape hatch used (label or commit/PR tag, as the tests-first gate defines them) → `hold`.
- [ ] A ticket with `Auto-merge: no` → `hold` regardless of other checks.
- [ ] Denylist scan: every added line in the PR is checked against entries from the `PEOPLE_DENYLIST` secret; a hit → `fail` whose reason names the file and line but never echoes the matched entry.
- [ ] When both `fail` and `hold` reasons exist the verdict is `fail`.
- [ ] A fixture per rule proves it fires; a test proves a denylist match is never printed.

## Comments
