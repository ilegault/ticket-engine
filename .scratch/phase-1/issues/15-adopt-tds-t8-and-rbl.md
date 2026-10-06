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
- [ ] With no action on the box, the box status issue shows RBL ready (no `Not ready:` entry for it) and the box claims RBL's first frontier ticket. Record under `## Comments` how many manual steps the whole run took, against the roughly fifteen it took for TDS-T8.

## Comments
