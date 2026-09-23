# 10: Bootstrap `adopt`, file side

**What to build:** Running the bootstrap in `adopt` mode on an existing repo rewrites it to the engine's conventions, safely and repeatably, without touching GitHub yet.

**Blocked by:** 04, 06

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] Legacy statuses (`complete`, `completed` → `done`; `human-task` → `ready-for-developer`) are migrated in every ticket file.
- [ ] The repo's own ticket skill is replaced by a pointer to the engine's skill; the repo's tests-first script is replaced by the engine's.
- [ ] It adds the repo engine config, the thin caller workflows (dispatch, integrity) pinned to an engine version, the ticket template (with `Runner` and `Auto-merge`), and `AGENTS.md` sections for the implementation protocol and the roles-not-people rule — leaving the rest of `AGENTS.md` untouched.
- [ ] It scans public files for likely names, Slack IDs and emails and prints a report; it does not rewrite them.
- [ ] Idempotent: a second run changes nothing; a developer-edited file is diffed and reported, never silently overwritten.
- [ ] Proven on fixture repos shaped like Slackbot, RBL and TDS-T8 (bold statuses, legacy words, an old skill, a Windows CI); assertions are on the resulting files.

## Comments
