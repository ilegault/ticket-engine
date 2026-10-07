# 82: The skill stops promising Jules its CI output, and the dead escalation path says it is Jules-only

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** `.scratch/box-fix-loop/spec.md`
**Binding:** ADR 0011 rule 6; ADR 0010 rule 1

## What to build

The ticket skill's Jules section tells Jules "If CI fails, you will receive the
failing output." Nothing sends it: the dispatcher's red-CI escalation path
(`EscalatePRAction` built in `DispatchCore.evaluate`, and
`LiveDispatcher.dispatch_escalations_and_stale_claims`) is never called by live
dispatch, which builds its snapshot without open PRs. Until a later effort handles
red CI on Jules PRs (ADR 0011 rule 6), the skill must not promise it, and the dead
path must say what it is. A repo can already be kept away from Jules with
`jules_enabled = false` in its `.ticket-engine.toml`; `DispatchCore.evaluate`
honours it.

- In `src/ticket_engine/resources/ticket_skill.md`, the bullet
  "**Respond to red CI by fixing.** If CI fails, you will receive the failing output. …"
  becomes "**Red CI.** The engine does not yet send you CI results. Before you
  finish, run every gate command yourself and fix what fails; never mute or weaken
  a test to do it."
- The docstrings of `EscalatePRAction` (`src/ticket_engine/dispatch.py`) and
  `dispatch_escalations_and_stale_claims` (`src/ticket_engine/live_dispatch.py`)
  each gain the sentence "Jules-only and not wired into live dispatch (ADR 0011
  rule 6); the box escalates its own PRs."

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests read the
real skill through `load_ticket_skill()`; nothing is faked.

- [ ] **No false promise.** Test `test_skill_does_not_promise_jules_the_ci_output` (in `tests/test_prompt.py`): the skill text does not contain `you will receive the failing`.
- [ ] **The replacement rule is there.** Test `test_skill_tells_jules_to_run_every_gate_command_itself`: the skill text contains `The engine does not yet send you CI results`.
- [ ] **The dead path says so.** Test `test_dispatcher_pr_escalation_is_documented_as_jules_only`: `EscalatePRAction.__doc__` and `LiveDispatcher.dispatch_escalations_and_stale_claims.__doc__` both contain `Jules-only and not wired into live dispatch`.

## Gate

In CI order (`.github/workflows/ci.yml`):

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Comments
