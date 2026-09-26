# 34: Glossary, issue-tracker doc and ADR 0007 match the built box

**What to build:** Bring the repo's binding docs in line with what tickets 19–31 built. Three things change:
- In `CONTEXT.md`:
  - *Quota reserve*: the local worker has no pre-flight reserve. It pauses on a quota error.
  - *Local worker*: takes any ticket.
  - *Checkpoint*: `agy` commits, the local worker pushes.
- `docs/agents/issue-tracker.md` documents the `Claimed-by:` line.
- ADR 0007 rule 3 gains **Variables: read** on the target repos, so the box can honour `TICKET_ENGINE_PAUSED`. The developer approved this during spec review.

Spec: `.scratch/box-primary-worker/spec.md` (§Held changes, Further Notes).

**Blocked by:** 31

**Status:** done

**Runner:** any

**Auto-merge:** no

This ticket changes `CONTEXT.md` and `docs/adr/`, so the integrity gate always holds it. The developer merges it by hand. That is intended.

## Acceptance criteria

These are doc edits. The proof is a new `tests/test_phase2_docs.py` that reads the three files as text. Nothing is faked. Write it first and watch it fail.

- [x] **Quota reserve.** `CONTEXT.md`'s `**Quota reserve**` entry no longer contains `20%`. It contains the sentence `Local worker: no pre-flight reserve (agy exposes no quota reading); it pauses on a quota error and keeps its claim.` The Jules half of the entry is unchanged.
- [x] **Local worker and Checkpoint.** The `**Local worker**` bullet under *Worker* says it takes any ticket and links to *Box*. The `**Checkpoint**` entry says `agy` commits at each criterion boundary and the local worker pushes. The test asserts both substrings: `takes any ticket` and `the local worker pushes`.
- [x] **Issue tracker.** `docs/agents/issue-tracker.md` gains, under *Conventions*, the bullet `` - A `Claimed-by:` line (`box` or `jules`) is written under `Status:` when a worker claims the ticket. Workers keep it; status words are unchanged. `` The test asserts the line is present.
- [x] **ADR 0007 amendment.** Rule 3 of `docs/adr/0007-the-box-runs-agy-unrestricted-inside-a-fenced-account.md` lists `Variables read` among the target-repo permissions. A new dated section `## Amendment — <YYYY-MM-DD>` explains in two or three sentences why: reading `TICKET_ENGINE_PAUSED` is the only non-public read the box needs, and without it the circuit breaker cannot stop the box. The test asserts `Variables` appears in rule 3 and the heading `## Amendment` exists.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

2026-09-26: Brought CONTEXT.md, docs/agents/issue-tracker.md, and ADR 0007 in line with tickets 19-31, proved by new `tests/test_phase2_docs.py`.
- AC1: `**Quota reserve**` rewritten to drop `20%` and state the local worker has no pre-flight reserve, pauses on a quota error, and keeps its claim; the Jules sentence is untouched; tested in `test_quota_reserve_has_no_pre_flight_reserve_for_local_worker`.
- AC2: the `**Local worker**` bullet under *Worker* now says it takes any ticket and links to *Box*; `**Checkpoint**` now says `agy` commits at each criterion boundary and the local worker pushes; tested in `test_local_worker_takes_any_ticket_and_links_to_box` and `test_checkpoint_says_agy_commits_and_local_worker_pushes`.
- AC3: `docs/agents/issue-tracker.md` gained the `Claimed-by:` bullet under *Conventions*, verbatim; tested in `test_issue_tracker_documents_claimed_by_line`.
- AC4: ADR 0007 rule 3 now lists `Variables` read on the target repos, and a dated `## Amendment — 2026-09-26` section explains why (`TICKET_ENGINE_PAUSED` is the one non-public read the box needs for the circuit breaker to reach it); tested in `test_adr_0007_rule_3_lists_variables_read` and `test_adr_0007_has_dated_amendment_section`.
- Gates: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q` (547 passed) all green on Python 3.12. Mutation checked by hand: reintroduced `20%` into the Quota reserve entry; `test_quota_reserve_has_no_pre_flight_reserve_for_local_worker` went red; reverted and reverified green.
- This change touches `CONTEXT.md` and `docs/adr/`, so the integrity gate always holds it (`Auto-merge: no` above, per AGENTS.md §10). The developer merges this one by hand.
