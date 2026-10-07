# 68: Bring Slackbot and TDS-T8 onto the new setup

**Status:** ready-for-developer

**Runner:** windows

**Auto-merge:** no

**Blocked by:** 67

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0009; ADR 0010

**An agent must not claim this ticket.** It needs the developer's `gh` login,
his merges, and the box.

## What to build

Slackbot and TDS-T8 were wired the old way: `box_enabled`, no install keys, and
`[[repos]]` blocks in the box's local config. This ticket runs the new tools on
both, which also proves `add-repo` is safe to re-run on a repo that is already
set up, before RBL is added from scratch (ticket 15).

## Acceptance criteria

- [ ] **The engine is live.** Tickets 53–67 merged. Move the `v1` tag to the
  engine's `master` the way ticket 36 did, so target repos' dispatch and integrity
  workflows run the new code. `engine-repos.toml` lists `ilegault/slackbot` and
  TDS-T8 with `box = true`.
- [ ] **The box runs the new code.** On the box as `agent`: `git pull` in the
  engine checkout, `pip install -e .`, delete every `[[repos]]` block from
  `~/.ticket-engine-local.toml`, add `projects_dir`, `envs_dir` and
  `baseline_timeout_minutes` if the defaults don't fit, restart the scheduled task.
  `box-worker.log` shows `tick:` lines and no `refuses to start`.
- [ ] **Both repos upgraded.** On the dev machine: `add-repo ilegault/slackbot --check`,
  then `add-repo ilegault/slackbot`; the same for TDS-T8. Review each upgrade PR
  (set `python_version` and `install` to what the repo really needs) and merge.
- [ ] **Both are ready on the box.** Within two status intervals the box status
  issue shows no `Not ready:` line, `C:\Users\agent\envs\` holds one environment
  per repo, and `box_readiness.json` records a passed baseline for each.
- [ ] **One gate list (ADR 0011).** Only after ticket 83 is merged and `v1` moved, and
  ticket 84 is merged. In each repo's upgrade PR: `install` is what CI installs
  (Slackbot: `pip install -r requirements-dev.txt`), and `gate_commands` lists every
  command its old test workflow ran, command for command, in CI order (Slackbot:
  `ruff check .`, `python scripts/check_tests_first.py`, `python tools/type_gate.py`,
  `pytest --tb=short -q -n auto --dist loadfile`). The old test workflow
  (Slackbot's `tests.yml`) is deleted in the same PR, and `.github/workflows/gate.yml`
  calls the engine's gate. (by hand)
- [ ] **Exactly two required checks.** In each repo's branch protection, remove the
  hand-set `Tests / lint` and `Tests / test (3.14)` (and TDS-T8's equivalents); the
  `engine-branch-protection` ruleset requires `ticket-engine/integrity-gate` and
  `gate / gate`. A test PR shows both checks and nothing else required. (by hand)
- [ ] **Re-running is a no-op.** `add-repo ilegault/slackbot --check` and the
  TDS-T8 equivalent both print `Nothing to do: ... is fully wired.` Record both
  outputs under `## Comments`, plus anything the run surprised you with.

## Gate

Not applicable (no code changes). Bench verification only.

## Out of scope

- RBL (ticket 15).
- Deleting the old Slackbot packages from the engine's own venv on the box.

## Comments
