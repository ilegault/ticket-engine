# 81: Switch the box to the launcher

**Status:** ready-for-developer

**Runner:** developer

**Auto-merge:** yes

**Blocked by:** 80

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0012

**An agent must not claim this ticket.**

## What to build

A hand step at the box, once tickets 79 and 80 are merged: move the box onto the
launcher so later engine changes reach it on their own.

## Acceptance criteria

- [ ] On the box, in the `agent` account: `git -C C:\Users\agent\ticket-engine pull --ff-only` and `.venv\Scripts\pip install -e .`. (by hand)
- [ ] In Task Scheduler, the box task's action is `box-launcher.exe` with the `--engine-dir` and `--state-dir` arguments from `docs/box-setup.md`; restart on failure stays on. (by hand)
- [ ] After a restart, the box status issue shows an `Engine:` line, and `launcher_state.json` in `logs_dir` names a `good_commit`. (by hand)
- [ ] Merging any small engine PR makes the box's `Engine:` line change within an hour without anyone touching the box. (by hand)

## Gate

Not applicable (no code changes).

## Comments
