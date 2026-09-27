# 40: LocalWorker falls back to Sonnet when agy is out of quota

**What to build:** `LocalWorker` gains an optional `sonnet_driver: SonnetDriver | None = None` constructor parameter and one new private method, `_start_with_fallback(prompt, cwd)`. Every one of `agy`'s four call sites now goes through it instead of calling `self.agy_driver.start(...)` directly: the first attempt in `run_one`, `_resume_from_checkpoint`, `_send_auto_reply`, and `fix_ci`. It tries `agy` first; only when the result's `quota_error` is `True` and a `sonnet_driver` is configured does it try Sonnet on the identical `prompt` and `cwd`, returning whichever result comes back. `_resolve_outcome`'s resume/auto-reply/escalation counters and `box_mode`'s return-to-caller pause behaviour are untouched — they only ever see the combined result of this one method. Spec: `.scratch/sonnet-quota-fallback/spec.md` (§`LocalWorker` changes).

**Blocked by:** 39

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests extend `tests/test_local_worker.py`, adding a fake `sonnet_driver` (a bare test double exposing `.start` and recording every call's `prompt`/`cwd`) alongside the existing fake `agy_driver`. `LocalWorker`, `assemble_prompt` and the parser stay real. Write these tests first and watch them fail.

- [x] **No `sonnet_driver` configured: unchanged.** With `sonnet_driver=None` (the default), a quota-error result from `agy_driver.start` passes through `_start_with_fallback` unchanged. Every existing quota-path assertion in `run_one`'s and `fix_ci`'s current tests still passes with `_start_with_fallback` in place of the direct call, with no expected values changed.
- [x] **agy quota, Sonnet configured and succeeds.** With a fake `agy_driver` returning a quota result and a fake `sonnet_driver` returning a success result, `_start_with_fallback(prompt, cwd)` returns the Sonnet result, and `sonnet_driver.start` was called with the identical `prompt` and `cwd` the `agy_driver` call received. In `run_one`, this success result drives the same PR-open and checkpoint-push calls an agy success would.
- [x] **agy quota, Sonnet also quota: falls through unchanged.** With both fakes returning quota results, `_start_with_fallback` returns the Sonnet result (still a quota result), and `_resolve_outcome`'s existing `box_mode`-return and non-`box_mode` sleep-and-resume branches trigger on it exactly as they do today on an agy-only quota result. Assert `agy_driver.start` and `sonnet_driver.start` were each called exactly once for that attempt — no repeated ping-pong between the two.
- [x] **agy succeeds or fails for a non-quota reason: Sonnet is never called.** For each of `success`, `waiting`, `timeout`, and `failed` outcomes from `agy_driver`, assert `sonnet_driver.start` records zero calls, and the existing resume/auto-reply/escalation behaviour for each outcome is unchanged.
- [x] **All four call sites go through the wrapper.** One assertion per site — `run_one`'s first attempt, `_resume_from_checkpoint`, `_send_auto_reply`, `fix_ci` — that a quota result from `agy_driver` at that site also reaches `sonnet_driver.start` when one is configured. `local_worker.py` contains no remaining direct call to `self.agy_driver.start(...)` outside `_start_with_fallback` itself.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

2026-09-27: Implemented `LocalWorker._start_with_fallback` and routed all four `agy` call sites through it (`run_one`'s first attempt, `_resume_from_checkpoint`, `_send_auto_reply`, `fix_ci`). Added `sonnet_driver: SonnetDriver | None = None` constructor param, defaulting to `None`.
- AC1: `test_start_with_fallback_no_sonnet_configured_passes_quota_through` and the full existing quota-path suite (e.g. `test_push_after_agy_quota_failure`, `test_quota_outcomes_never_count_toward_resumes_or_escalate`) pass unchanged with no sonnet_driver configured.
- AC2: `test_start_with_fallback_agy_quota_sonnet_succeeds` asserts the Sonnet result is returned and Sonnet received the identical prompt/cwd; `test_run_one_first_attempt_falls_back_to_sonnet_on_quota` drives it through `run_one`.
- AC3: `test_start_with_fallback_agy_quota_sonnet_also_quota` asserts each driver is called exactly once and the combined quota result is Sonnet's.
- AC4: `test_start_with_fallback_non_quota_never_calls_sonnet`, parametrized over `success`/`waiting`/`timeout`/`failed`.
- AC5: `test_run_one_first_attempt_falls_back_to_sonnet_on_quota`, `test_resume_from_checkpoint_falls_back_to_sonnet_on_quota`, `test_send_auto_reply_falls_back_to_sonnet_on_quota`, `test_fix_ci_falls_back_to_sonnet_on_quota` (one per call site); `test_no_direct_agy_driver_start_calls_outside_fallback` asserts the only remaining `self.agy_driver.start(` call in `LocalWorker`'s source is inside `_start_with_fallback`.
- Gates run under Python 3.12 (see ticket 39's comment on the container's 3.11 `pytest`): `ruff check .` clean, `check_tests_first.py` OK, full suite 590 passed (12 new tests added).
