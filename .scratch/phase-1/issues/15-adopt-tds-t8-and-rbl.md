# 15: Add RBL with `add-repo` (first run from scratch)

**What to build:** RBL is wired to the engine with the `add-repo` command and the box sets it up on its own, as the first real run of the new setup from scratch. (Rewritten 2026-10-06: TDS-T8 was adopted by hand and is brought forward by ticket 68 in `.scratch/add-repo/`; the per-step manual criteria this ticket used to hold are replaced by `add-repo`, ADR 0009 and ADR 0010.)

**Blocked by:** 68

**Status:** ready-for-developer

**Runner:** windows

**Auto-merge:** no

**An agent must not claim this ticket.** It needs the developer's `gh` login, his token settings, his merges and the Jules web UI.

## Acceptance criteria

- [ ] `add-repo ilegault/RBL --check` run first; its plan recorded under `## Comments`. Use the exact owner/name as GitHub shows it.
- [ ] Whether RBL's suite passes on Linux decided before the real run: if not, run with `--no-jules` (the repo config then holds `jules_enabled = false` and Jules never takes its tickets).
- [ ] `add-repo ilegault/RBL` run to completion: RBL added to `PIPELINE_TOKEN` and to the box's token, the adopt PR and the repo-list PR reviewed and merged, ruleset created, dispatcher dry run clean, Jules script pasted (unless `--no-jules`).
- [ ] RBL's adopt PR sets `python_version` and `install` to what RBL really needs, and RBL's AGENTS.md §11 CI-watching step is checked to be consistent with the engine skill (the developer edits AGENTS.md; the bootstrap does not).
- [ ] Before the real run, the engine's shared `gate` and integrity workflows can run RBL's checks on Windows. Today both are `runs-on: ubuntu-latest`, while RBL's own CI is `windows-latest`; on 2026-11-10 RBL's suite on Linux crashed twice in a Qt test (segfault) and hung at 99%, and `tools/type_gate.py` counted 140 against the ratchet's 139. This is engine design work: run `/grill-with-docs` on it before planning (does check 7's run against the base code move to Windows too?). `--no-jules` does not help; it only keeps Jules away.
- [ ] The adopt step's privacy scan passes on RBL. It stopped the 2026-11-10 run on five files: three are real emails (`README.md` line 116, `docs/AMP_ENVELOPE_AND_HV_SAFETY_PLAN.md` line 5, `docs/UI_FIXES_CAPTURE_QUALITY_AND_RASTER_RESTRUCTURE.md` line 5) and must be removed or replaced by a role; two are false positives of `_SLACK_ID_RE` in `src/ticket_engine/bootstrap.py`, which matches an all-caps word ending a line (`src/rbl/config/characterization_history.py` line 168, `src/rbl/hardware/dose_model.py` line 14). Fix the regex in the engine (a ticket of its own) rather than rewording RBL's code.
- [ ] RBL's default branch carries a hand-written `.ticket-engine.toml` (added 2026-11-10 so local agents follow the engine's rules). With that file present, `plan_add_repo` plans neither the adopt PR nor an upgrade PR, so `add-repo` would never install the engine's workflows. Before the real run, either delete the file from RBL's default branch or change `plan_add_repo` to plan the adopt PR whenever the integrity workflow is missing; decide which when this ticket is picked up.
- [ ] With no action on the box, the box status issue shows RBL ready (no `Not ready:` entry for it) and the box claims RBL's first frontier ticket. Record under `## Comments` how many manual steps the whole run took, against the roughly fifteen it took for TDS-T8.

## Comments

2026-11-10 — `add-repo ilegault/RBL --check` printed: set_secret JULES_API_KEY, set_secret PIPELINE_TOKEN, create_label engine:hold, create_label engine:escalated, create_label engine:windows-waiting, adopt_pr, list_pr, wait_adopt, create_ruleset engine-branch-protection, dry_run, jules_script.

2026-11-10 — `add-repo ilegault/RBL --no-jules` was then run. It applied the settings steps (repo secrets and the three labels; harmless, and a later run skips them) and stopped at adopt_pr on the privacy scan (five files, see the criteria). Nothing was pushed: no `engine/add-repo` branch, no adopt PR, no repo-list PR, no ruleset. RBL is not in `engine-repos.toml` and the box does not touch it. Decision the same day: RBL gets the engine's rules locally only (a hand-written `.ticket-engine.toml` read by the implement-ticket-parallel skill); full wiring waits for this ticket.
