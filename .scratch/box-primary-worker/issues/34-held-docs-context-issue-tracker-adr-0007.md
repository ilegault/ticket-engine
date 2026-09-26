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

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

This ticket changes `CONTEXT.md` and `docs/adr/`, so the integrity gate always holds it. The developer merges it by hand. That is intended.

## Acceptance criteria

These are doc edits. The proof is a new `tests/test_phase2_docs.py` that reads the three files as text. Nothing is faked. Write it first and watch it fail.

- [ ] **Quota reserve.** `CONTEXT.md`'s `**Quota reserve**` entry no longer contains `20%`. It contains the sentence `Local worker: no pre-flight reserve (agy exposes no quota reading); it pauses on a quota error and keeps its claim.` The Jules half of the entry is unchanged.
- [ ] **Local worker and Checkpoint.** The `**Local worker**` bullet under *Worker* says it takes any ticket and links to *Box*. The `**Checkpoint**` entry says `agy` commits at each criterion boundary and the local worker pushes. The test asserts both substrings: `takes any ticket` and `the local worker pushes`.
- [ ] **Issue tracker.** `docs/agents/issue-tracker.md` gains, under *Conventions*, the bullet `` - A `Claimed-by:` line (`box` or `jules`) is written under `Status:` when a worker claims the ticket. Workers keep it; status words are unchanged. `` The test asserts the line is present.
- [ ] **ADR 0007 amendment.** Rule 3 of `docs/adr/0007-the-box-runs-agy-unrestricted-inside-a-fenced-account.md` lists `Variables read` among the target-repo permissions. A new dated section `## Amendment — <YYYY-MM-DD>` explains in two or three sentences why: reading `TICKET_ENGINE_PAUSED` is the only non-public read the box needs, and without it the circuit breaker cannot stop the box. The test asserts `Variables` appears in rule 3 and the heading `## Amendment` exists.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
