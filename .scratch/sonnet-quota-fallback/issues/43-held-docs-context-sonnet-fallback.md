# 43: Glossary matches the built Sonnet fallback

**What to build:** Bring `CONTEXT.md` in line with what tickets 39-41 built.
- *Quota reserve*: gains a sentence that the local worker's quota pause is now only reached once an optional Sonnet fallback has also been tried and also reports quota, when one is configured.
- *Local worker*: notes it may optionally fall back to a Claude Sonnet session on an agy quota error, still one ticket, one PR, unchanged.

Spec: `.scratch/sonnet-quota-fallback/spec.md` (§Further Notes, suggested ticket order item 5).

**Blocked by:** 41

**Status:** done

**Runner:** any

**Auto-merge:** no

This ticket changes `CONTEXT.md`, so the integrity gate always holds it. The developer merges it by hand. That is intended.

## Acceptance criteria

The proof is a new `tests/test_sonnet_fallback_docs.py` that reads `CONTEXT.md` as text, the same shape as `tests/test_phase2_docs.py`'s reads of `CONTEXT.md`/`docs/adr/`. Write it first and watch it fail.

- [x] **Quota reserve.** The `**Quota reserve**` entry's local-worker sentence is extended (not replaced) to say the pause is reached only once a configured Sonnet fallback has also reported quota. The test asserts the existing sentence `Local worker: no pre-flight reserve (agy exposes no quota reading); it pauses on a quota error and keeps its claim.` is still present, plus a new substring naming the Sonnet fallback.
- [x] **Local worker.** The `**Local worker**` bullet gains a clause that it may fall back to a Claude Sonnet session on an agy quota error, still one ticket, one PR. The test asserts the substring `Sonnet` appears in that entry.
- [x] **No other CONTEXT.md entry changes.** The test snapshots every other glossary entry's text (a simple line-count or hash check against the two edited entries only) so an unrelated wording drift elsewhere in the file fails this ticket's test rather than being missed.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

2026-09-27: Extended `CONTEXT.md`'s **Quota reserve** and **Worker**/**Local worker** entries to name the optional Sonnet fallback built in tickets 39-41, leaving every other entry untouched.
- AC1: `test_context_quota_reserve_extended_not_replaced` asserts the existing local-worker sentence is still present verbatim, plus a new clause naming the Sonnet fallback and its own quota reporting.
- AC2: `test_context_local_worker_names_sonnet_fallback` asserts the `Worker`/`Local worker` paragraph contains `Sonnet` and `one ticket, one PR`.
- AC3: `test_context_no_other_entries_changed` splits `CONTEXT.md` into blank-line-delimited paragraphs, hashes every paragraph except the two edited ones, and compares against a stored snapshot (`tests/fixtures/sonnet_fallback_docs/context_md_paragraph_hashes.json`) plus a total-paragraph-count check.
- New test file `tests/test_sonnet_fallback_docs.py`, mirroring `tests/test_phase2_docs.py`'s plain-text-read pattern.
- This PR touches `CONTEXT.md`, so it is always held (AGENTS.md §7/§10 rule 2) — `Auto-merge: no` is set and this ticket is a leaf; the developer merges it by hand.
- Gates run under Python 3.12 (see ticket 39's comment on the container's 3.11 `pytest`): `ruff check .` clean, `check_tests_first.py` OK, full suite 599 passed (3 new tests added).
