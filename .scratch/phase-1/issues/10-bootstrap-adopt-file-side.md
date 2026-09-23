# 10: Bootstrap `adopt`, file side

**What to build:** Running the bootstrap in `adopt` mode on an existing repo rewrites it to the engine's conventions, safely and repeatably, without touching GitHub yet.

**Blocked by:** 04, 06

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [x] Legacy statuses (`complete`, `completed` → `done`; `human-task` → `ready-for-developer`) are migrated in every ticket file.
- [x] The repo's own ticket skill is replaced by a pointer to the engine's skill; the repo's tests-first script is replaced by the engine's.
- [x] It adds the repo engine config, the thin caller workflows (dispatch, integrity) pinned to an engine version, the ticket template (with `Runner` and `Auto-merge`), and `AGENTS.md` sections for the implementation protocol and the roles-not-people rule — leaving the rest of `AGENTS.md` untouched.
- [x] It scans public files for likely names, Slack IDs and emails and prints a report; it does not rewrite them.
- [x] Idempotent: a second run changes nothing; a developer-edited file is diffed and reported, never silently overwritten.
- [x] Proven on fixture repos shaped like Slackbot, RBL and TDS-T8 (bold statuses, legacy words, an old skill, a Windows CI); assertions are on the resulting files.

## Comments

Built `src/ticket_engine/bootstrap.py` — pure `adopt()` core + `run_adopt()` adapter.
- Criterion 1: `migrate_ticket_text()` + per-ticket FileWrite; 8 tests.
- Criterion 2: skill-file pointer replacement + tests-first script replacement; 5 tests.
- Criterion 3: `_handle_template_file()` for config/workflows/template; `_handle_agents_md()` for sections; 9 tests.
- Criterion 4: `scan_pii()` finds emails and Slack IDs, never echoes matched text; 5 tests.
- Criterion 5: second run → no writes; developer edit → FileDiff, not overwritten; 2 tests.
- Criterion 6: 13 fixture-repo tests (Slackbot, RBL, TDS-T8) asserting resulting files.
Gate: ruff clean, tests-first gate OK, 167 tests pass.
