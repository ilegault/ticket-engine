# 25: Jules continues a released box ticket from its checkpoint

**What to build:** When the dispatcher starts Jules on a `Runner: any` ticket and the box has already pushed a ticket branch for it (`ticket/<effort>-<NN>-<slug>` exists and differs from the default branch head), the Jules prompt carries a handoff section. That section names the branch and the progress note from that branch's ticket file, and tells the worker to fetch and merge it, then continue from the note's `Next:`. If the fetch fails, the worker starts the ticket fresh. With no box branch, the prompt is unchanged. The Jules session still starts from the default branch, so the PR base stays correct. Spec: `.scratch/box-primary-worker/spec.md` (§Handoff). ADR 0006 rule 6.

**Blocked by:** 23, 24

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in `tests/test_prompt.py`, `tests/test_dispatch_scenarios.py` and `tests/test_live_dispatch.py`. `assemble_prompt`, `DispatchCore` and the parser are real. The GitHub and Jules clients are the existing fakes. Write these tests first and watch them fail.

- [x] **Prompt.** `assemble_prompt(skill_text, repo, ticket_path, handoff: Handoff | None = None)`, where `Handoff` is a frozen dataclass with `branch` and `note`. With a handoff, the prompt contains a section headed `## HANDOFF — CONTINUE FROM A CHECKPOINT` placed before `## FINAL STEP`. It contains these exact lines:
  - `A previous worker pushed a checkpoint to branch <branch>.`
  - `Run: git fetch origin <branch> && git merge --no-edit FETCH_HEAD`
  - `If that fails, start the ticket fresh from the default branch and say so under ## Comments.`
  - the note, verbatim.

  Without a handoff, the output is byte-identical to today's. The existing `tests/test_prompt.py` tests pass unchanged.
- [x] **Core carries it.** `WorldSnapshot` gains `checkpoints: dict[int, Handoff]`, filled by the adapter. `StartTicketAction` gains `handoff: Handoff | None = None`. `evaluate` copies `checkpoints.get(ticket.number)` onto the start action of a `Runner: any` ticket. A test asserts the action's `handoff` for a ticket with a checkpoint, and `None` for one without.
- [x] **Live detection.** Before `evaluate`, `LiveDispatcher.dispatch` checks each frontier ticket that has no claim. If `get_branch_head_time` (ticket 23) finds its ticket branch and that branch's head SHA differs from `base_sha`, the dispatcher reads the ticket file on that branch. It extracts the note with `local_worker._extract_progress_note`; move that function to `prompt.py` and re-export it from `local_worker`, so the dispatcher does not import the orchestrator. The result goes into `checkpoints`. The live test asserts the prompt sent to the fake `create_session` contains the branch name and the note, and that `starting_branch` is still the default branch.
- [x] **Windows tickets are never handed off.** A `Runner: windows` ticket with a checkpoint never produces a `StartTicketAction`, in any box state. This is asserted in `tests/test_dispatch_scenarios.py`.
- [x] **Docstring.** `prompt.py`'s `WHY THIS EXISTS` explains why the session starts from the default branch rather than the checkpoint branch (the PR base must be the default branch), and that the fetch is best-effort by design. Dropping the handoff argument in `dispatch` turns the live test red; check this by hand once before landing.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

2026-09-26: Resumed this ticket on branch `ticket/box-primary-worker-25-handoff-to-jules`
from the existing claim commit. Found master's live_dispatch.py/run_report.py
missing the box-status wiring a prior merge had dropped (undefined `box` name,
missing `RunFacts` fields); restored that in a separate commit first so the
three gates could run at all, since ticket 25 edits the same function.

Built the feature: `Handoff(branch, note)` frozen dataclass in `dispatch.py`;
`assemble_prompt` takes an optional `handoff` and, when given, inserts
`## HANDOFF — CONTINUE FROM A CHECKPOINT` (with the fetch/merge command, the
best-effort fallback line, and the note verbatim) before `## FINAL STEP` —
verified byte-identical output with no handoff. `WorldSnapshot.checkpoints`
and `StartTicketAction.handoff` added; `DispatchCore.evaluate` attaches the
matching checkpoint only when building a `StartTicketAction`, which windows
tickets never reach (asserted for both box-absent and box-available states).
Moved `_extract_progress_note` from `local_worker.py` to `prompt.py` as
`extract_progress_note`, re-exported from `local_worker` under its old name.
`LiveDispatcher.dispatch` (step 4e, before building the snapshot) now checks
each unclaimed frontier ticket's `ticket/<effort>-<NN>-<slug>` branch via
`get_branch_head_time`, compares its head SHA (via `get_default_branch_sha`)
against `base_sha`, and on a real divergence reads the ticket file at that
ref and extracts its progress note into `checkpoints`. The session still
starts from `self.config.default_branch`. Confirmed by hand that removing
`handoff=action.handoff` from the `assemble_prompt` call in `live_dispatch.py`
turns `test_live_dispatch_hands_off_box_checkpoint_to_jules` red, then
restored it.

Tests: `tests/test_prompt.py` (handoff section content/placement, no-handoff
byte-identity, `extract_progress_note`, the `local_worker` re-export),
`tests/test_dispatch_scenarios.py` (checkpoint attached only to the matching
ticket's action; windows ticket with a checkpoint never starts, box-absent
and box-available), `tests/test_live_dispatch.py` (checkpoint branch found →
prompt carries branch + note + `starting_branch` unchanged; no checkpoint
branch → prompt unchanged; windows ticket with a checkpoint never starts a
session). All written and watched failing before the corresponding
implementation landed. Gates green: `ruff check .`, `check_tests_first.py`,
`pytest -q` (432 passed).
