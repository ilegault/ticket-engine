# 38: Any worker checks claimability against the live remote before claiming

**What to build:** The race this effort just hit: a session working a ticket
outside the dispatcher's own loop (an ad hoc human/agent session, not a
Jules or box run `DispatchCore.evaluate` started) began orienting on ticket 28
at the same moment the box legitimately claimed, implemented and merged it.
Nothing was lost — the session's own orientation re-check caught the
staleness before it wrote a line of code — but the ticket skill never *made*
that check mandatory or automatic; it happened to be careful in that
instance and would not reliably in another. Give every worker, whatever
started it, one pure decision plus one small script to run immediately
before creating a claim branch, so "is this ticket still actually free"
stops being a matter of individual carefulness.

- **Pure decision.** `dispatch.claim_precheck(ticket: Ticket | None, claim_refs: Sequence[str | Claim]) -> str`
  returns `"unreadable"` when `ticket is None`; `"already-done"` when
  `ticket.is_done()`; `"already-claimed"` when `ticket.status != "ready-for-agent"`
  or `dispatch.is_ticket_claimed(ticket, claim_refs)` is true; `"claimable"`
  otherwise. Reuse `is_ticket_claimed` — do not write a second claimed-check
  (AGENTS.md §3 invariant 3's "never write a third copy" applies to this check
  as much as to the frontier rule).
- **Script.** `scripts/check_claimable.py` has `main(run_fn, effort: str, number: int, default_branch: str = "master") -> int`.
  It runs three git commands through the injected `run_fn(args: list[str]) -> subprocess.CompletedProcess`
  (never a bare `subprocess.run` call in the script body):
  1. `git fetch origin <default_branch>`
  2. `git ls-tree -r --name-only origin/<default_branch> .scratch/<effort>/issues/` to find the
     one file matching `NN-*.md` for the given number (zero-padded), reading it
     with `git show origin/<default_branch>:<path>` and parsing it with the real
     `TicketParser`. No match, or more than one, is `unreadable`.
  3. `git ls-remote --heads origin claim/<effort>/<NN> ticket/<effort>-<NN>-*` — its
     output lines become the `claim_refs` passed to `claim_precheck` (empty output
     is an empty list, not an error).
  `main` prints exactly one line, `<decision>: <effort> <NN>` (e.g.
  `claimable: box-primary-worker 38`), and returns `0` for `"claimable"`, `1`
  for anything else. A worker (or a human) runs it and checks the exit code;
  it makes no GitHub API call and needs no token, only the git remote the
  worktree already has.
- **Ticket skill.** In `src/ticket_engine/resources/ticket_skill.md`, the
  "Claim the ticket" step under §1 gets a first bullet, before "Create a
  ticket branch": "Run `python scripts/check_claimable.py <effort> <NN>`
  immediately before creating the claim branch. Anything other than
  `claimable: ...` means someone else already has this ticket, or it landed
  while you were orienting — stop, do not claim it, and end the session with
  a one-line summary naming the decision it returned." The "Work the
  frontier" section keeps its existing rule about `Blocked by:` and
  `ready-for-developer`; this is an additional, later check against the live
  remote, not a replacement for it.

Spec: none yet for this cross-cutting concern — this ticket's acceptance
criteria are the spec. ADR 0006 rule 4 (`Claimed-by` recording) is the
existing convention this reuses; this ticket does not change it.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in `tests/test_dispatch_scenarios.py` (the pure decision, built
through the real parser as its existing `ticket(...)`-style helpers do) and a
new `tests/test_check_claimable.py` (the script, with a fake `run_fn` that
records the argument lists it was called with, in order, and returns scripted
`CompletedProcess`-like results — real network and real git are never
touched). Write these tests first and watch them fail.

- [ ] **Four decisions, pure.** `claim_precheck`: `None` → `"unreadable"`;
  a ticket built with `status="done"` → `"already-done"`; a ticket with
  `status="ready-for-agent"` and `claim_refs=["claim/box-primary-worker/38"]`
  → `"already-claimed"`; the same ticket with `status="in-progress"` and
  `claim_refs=[]` → `"already-claimed"` (status alone is enough, no claim
  branch needed); a ticket with `status="ready-for-agent"` and `claim_refs=[]`
  → `"claimable"`.
- [ ] **Script drives the three commands in order and parses the real file.**
  With a fake `run_fn` scripted to return one matching path from
  `ls-tree`, that file's real content from `git show`, and one matching ref
  from `ls-remote`, `main(fake_run_fn, "box-primary-worker", 38)` returns `1`
  and prints `already-claimed: box-primary-worker 38`. Assert the three
  recorded argument lists, in order, match the commands above with `38`
  zero-padded to `38` and the effort substituted correctly for a
  double-digit number (also test number `7` renders `07` in the paths and
  refs).
- [ ] **No match is `unreadable`, not a crash.** `ls-tree` returning no
  matching path, or two, makes `main` return `1` and print
  `unreadable: <effort> <NN>` without calling `git show` or `git ls-remote`.
- [ ] **Claimable exits 0.** With `ls-remote` returning empty output and the
  real ticket status `ready-for-agent`, `main` returns `0` and prints
  `claimable: <effort> <NN>`.
- [ ] **Skill text.** A test loads `ticket_skill.md` as text and asserts the
  "Claim the ticket" section contains the exact sentence "Run
  `python scripts/check_claimable.py <effort> <NN>` immediately before
  creating the claim branch." and the phrase "stop, do not claim it". Nothing
  else in the skill file is reworded by this ticket.
- [ ] **Nothing existing is weakened.** `is_ticket_claimed` is unchanged in
  behavior (reused, not edited past what's needed to accept a plain `str`
  claim-ref list as it already does), and every existing test in
  `tests/test_dispatch_scenarios.py` and `tests/test_local_worker.py` passes
  unchanged.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
