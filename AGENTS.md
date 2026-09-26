# AGENTS.md — orientation for AI sessions working on ticket-engine

## Agent skills

### Issue tracker

Issues live as local markdown files under `.scratch/<effort>/`. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context layout: one `CONTEXT.md` at the repo root, ADRs in `docs/adr/`. See `docs/agents/domain.md`.

ticket-engine is the machinery that takes a planned ticket set in a **target
repo** and gets it implemented by agents: overnight, in dependency order, merged
only when the tests pass honestly. It computes the frontier, claims tickets,
starts Jules sessions, judges PRs with the integrity gate, escalates, pauses, and
reports.

**Read this before touching code.** Then read the module docstring of whatever
file you are about to edit. This codebase puts its *reasoning* in `WHY THIS EXISTS`
docstrings, not in a wiki.

**This repo decides what gets merged into other repos without a human looking.**
A dispatcher bug starts the wrong ticket; an integrity bug lets a muted test
through to a default branch; a report bug tells the developer something is
actionable when it is not. So the conventions here are strict, and a green suite
made green by editing a test is worse than a red one.

Three kinds of session read this file:

- **The planner** (Claude, in Cowork or Claude Code) grills designs and writes
  specs and tickets. It never implements. Read §10.
- **A worker** (Jules, or the Antigravity CLI) implements one ticket from this
  repo's own `.scratch/`. Read §9.
- **Any session** fixing something the developer asked for directly. Read all of it.

---

## 1. Quick facts

| | |
|---|---|
| Language / runtime | Python 3.12+ (CI and Jules run 3.12). Standard library only: `dependencies = []` |
| Package | `src/ticket_engine/` — about 5 800 lines across 17 modules |
| Tests | `pytest`, in `tests/`, with fixtures in `tests/fixtures/` |
| Console scripts | `dispatch`, `integrity-gate`, `work-windows` (see `pyproject.toml`) |
| Repo | `github.com/ilegault/ticket-engine`, default branch `master`. **Public** (ADR 0002). |
| Local folder | The developer's clone is `PycharmProjects/ticket-engine`. A stray folder named `ticker-engine` (sic) is **not** the repo. |
| Release | Target repos call the reusable workflows pinned to the tag **`v1`**. See §2. |

```
# setup
python -m venv .venv
.venv\Scripts\activate          # Windows; source .venv/bin/activate on Linux
pip install -e . ruff pytest

# run the gate, in the order CI runs it (.github/workflows/ci.yml)
ruff check .
python scripts/check_tests_first.py
pytest -q

# see what the dispatcher would do against a target clone, with no live actions
dispatch --dry-run <path-to-target-clone>
```

This repo's CI runs **three** gates, the ones above. There is no type gate here.
Target repos may run more; that is their `AGENTS.md`'s business, not this one's.

---

## 2. Where this repo sits

**Engine vs target repo.** Target repos (Slackbot first; TDS-T8 and RBL later)
hold their own code, tickets, `AGENTS.md`, `CONTEXT.md` and ADRs. They call this
repo through thin caller workflows:

```yaml
uses: ilegault/ticket-engine/.github/workflows/dispatch.yml@v1
```

and `pip install "ticket-engine @ git+https://github.com/ilegault/ticket-engine.git@v1"`.

**A merge to `master` does not reach any target repo.** Target repos run whatever
commit `v1` points at. Moving `v1` to a new commit is a developer step, done after
merge. Never write a ticket, an acceptance criterion or a verification step that
says a target repo "now shows" a change, unless that step is explicitly the
developer's and names moving the tag.

**The engine also works itself.** `.github/workflows/dispatch.yml` runs on this
repo (push, hourly, manual) and installs the engine from the checkout
(`pip install -e .`) rather than from `v1`. So tickets under this repo's
`.scratch/` go through the same frontier, claims and Jules sessions as any target
repo's.

**The morning report** is one pinned issue in this repo, rewritten daily at
12:00 UTC by `.github/workflows/morning-report.yml` →
`scripts/run_morning_report.py`. Which target repos it covers comes only from
`engine-repos.toml`.

---

## 3. Layered architecture — the one rule that matters

**Three pure cores. Thin adapters around them. Every decision lives in a core.**

A core is a function from plain data to plain data: no network, no subprocess, no
file writes, no clock reads, no environment reads. An adapter does the I/O and
nothing else. It fetches the world, hands it to the core, and carries out
what the core returns. This split is what lets every scheduling decision and every
verdict be tested from a static snapshot, and it is a **requirement**, not a style.

| Layer | Module | What it owns |
|---|---|---|
| Parse | `parser.py` | `Ticket`, `TicketParser`. Status, Blocked by, Runner, Auto-merge; legacy words become `ParseFinding`s, never `done` |
| Config | `config.py` | `RepoConfig` from `.ticket-engine.toml`. Every tunable's one home |
| **Dispatch core** | `dispatch.py` | `DispatchCore`: `compute_frontier`, `evaluate` → actions. `WorldSnapshot` in, `DispatchResult` out. `evaluate_waiting_sessions`: answer or escalate a Jules session waiting on a question (ADR 0004) |
| **Integrity core** | `integrity.py` | `IntegrityCore`: the seven checks of ADR 0001 + denylist → `IntegrityVerdict` |
| **Bootstrap core** | `bootstrap.py` | `adopt`, `new`, `github_setup` → files to write and GitHub operations to perform |
| Prompt | `prompt.py` | `assemble_prompt`: ticket skill + ticket path + orientation order. Pure |
| Ticket lint | `ticket_lint.py` | `lint_ticket`: reasons a ticket cannot land as written (ADR 0005). Pure. The dispatcher does not start a ticket with findings |
| Reports | `run_report.py`, `morning_report.py` | `build_run_report`, `render_morning_report`. Pure renderers |
| Adapters | `github.py`, `jules.py`, `agy.py` | REST/GraphQL, the Jules API, the Antigravity CLI. Thin, injectable, faked in tests |
| Orchestrators | `live_dispatch.py`, `integrity_runner.py`, `local_worker.py`, `cli.py`, `scripts/` | Wire adapters to cores. Do I/O. Decide nothing a core should decide |
| Worker instructions | `src/ticket_engine/resources/ticket_skill.md` | The runner-agnostic skill every worker receives in its prompt |

### Invariants the design rests on

1. **Cores are pure.** If a new rule needs data a core does not have, add a field
   to the snapshot/input record and have the adapter fill it. Never have the core
   fetch it.
2. **The dispatcher keeps no state of its own.** Everything it knows it reads from
   GitHub and the Jules API on each run: claim branches, PRs, labels, sessions, the
   `TICKET_ENGINE_PAUSED` variable. No state file, no database, no cache between runs.
3. **One definition of "blocked".** The frontier rule is in
   `DispatchCore.compute_frontier`: a ticket is on the frontier only if it is
   `ready-for-agent` and every `Blocked by` ticket exists and `is_done()`. Anything
   else that needs to know whether a ticket can start (reports, the local worker)
   either calls into that or into `run_report.unfinished_blockers`, which mirrors
   it and says so. **Never write a third copy.** Two copies that drift make the
   report contradict the dispatcher.
4. **An unknown or legacy status is never `done`.** `Ticket.is_done()` refuses any
   ticket with a status finding. Keep it that way.
5. **Nothing secret or personal is ever printed.** Actions logs are public
   (ADR 0002). No prompt, token, API response body or denylist entry reaches a log,
   a step summary, a PR comment or an issue. HTTP status lines are fine.
6. **Every tunable has one home: `RepoConfig` / `.ticket-engine.toml`** (or
   `local_config.py` for the local worker). No literal limits in logic.

---

## 4. Surfaces the developer reads

The developer mostly meets this engine through text it writes. Those surfaces are
product, and they are tested like product.

| Surface | Written by | Where it appears |
|---|---|---|
| Run report | `run_report.build_run_report` | The dispatch run's Actions summary page |
| Morning report | `morning_report.render_morning_report` | The pinned issue in this repo |
| Integrity verdict | `integrity_runner.format_verdict_comment` | A PR comment and commit status |
| Escalation brief | `dispatch.assemble_escalation_brief` | The ticket's `## Comments`, on the PR branch |

Rules for every one of them:

- **Say why, not just what.** "Started 0 tickets" must come with the reason: paused,
  a limit, or everything waiting on something.
- **Never make a ticket look actionable when it is not.** Any ticket listed for the
  developer shows both what it is **waiting on** (its unfinished blockers, with
  their statuses) and what it is **holding up**. A `ready-for-developer` ticket
  whose blockers are unfinished is not something the developer can do yet, and the
  report must say so.
- Ticket numbers are zero-padded to two digits (`run_report._label`). Use it.
- Tests assert on the rendered text, whole table rows where there is a table, the
  way `tests/test_run_report.py` does. Not on intermediate lists.

---

## 5. Conventions to follow

- **Module docstrings explain WHY**, under a `WHY THIS EXISTS` heading: the spec
  section, ticket, ADR, bug or API constraint that forced the design.
  `github.py`'s note on why auto-merge goes through GraphQL rather than an
  immediate REST merge is the model.
- **`logging.getLogger(__name__)`**. Never `print()` in `src/`. The CLI's
  user-facing output goes through the CLI entry points only.
- **Never swallow an exception.** Handle it explicitly, or let it rise with context.
  The run report's `pause_check_error` shows the pattern: a failed read is
  recorded and shown, not silently treated as "fine".
- **Standard library only.** `dependencies = []` is deliberate: the engine is
  pip-installed from git into every target repo's Actions run. Adding a dependency
  is a design decision for the developer. Escalate it; do not add it.
- **Adapters are injectable.** Every I/O seam takes a callable or client so tests
  pass a fake (`run_fn`, `fetch_fn`, the GitHub and Jules clients). Copy that
  shape for any new adapter.
- **Roles, never people** (ADR 0002 and the section at the end of this file).
  Tests, fixtures, docs, tickets and commit messages included.

---

## 6. Testing

- **A good test drives a core from the outside and asserts what the engine would
  do in the world.** Which tickets it starts, what verdict it returns, which files
  and settings it produces, what text the developer reads. Never how it computed
  that.
- **Build tickets through the real parser**, as `tests/test_run_report.py` does
  with its `ticket(...)` helper, so statuses and blockers are read exactly as in
  production.
- **Adapters are faked; cores and the parser are real.** No test calls the live
  GitHub or Jules API, spawns a real `agy`, or reads the real clock. Recorded API
  responses live in `tests/fixtures/github` and `tests/fixtures/jules`.
- **Integrity checks are tested with fixture base/PR pairs** (`tests/fixtures/integrity`):
  one fixture proves a check fires, a clean one proves it passes. Check 7 uses a
  real tiny project whose new test does and does not fail on base.
- **Bootstrap tests run on copies of fixture repos**, and run twice to prove the
  second run changes nothing.
- **Every test must be seen to fail.** Break the rule it guards and watch it go red.
  The integrity gate's check 7 enforces this mechanically for target repos. Hold
  this repo to the same standard by hand.

---

## 7. Known traps

- **Ticket numbers are unique across the whole repo, not per effort.** Blockers and
  claim branches key tickets by number alone, and `run_report.find_duplicate_numbers`
  reports collisions. A new effort **continues numbering from the highest existing
  ticket number anywhere under `.scratch/`**. It does not restart at `01`. Phase 1
  used 01–15, so the next ticket here is 16.
- **The `v1` tag, again.** A fix to the dispatcher or the reports is invisible in
  Slackbot until the developer moves `v1`. "Merged" is not "live".
- **Line endings.** This repo has no `.gitattributes`, and a Windows checkout can
  show every file as modified with no content change. `git diff --ignore-cr-at-eol`
  tells you whether a diff is real. Never commit a CRLF-only churn.
- **The integrity gate holds its own inputs.** A target-repo PR touching
  `.github/`, gate scripts, `docs/adr/`, `AGENTS.md` or `CONTEXT.md` is always held
  (ADR 0001 check 4). That is intended. Do not "fix" it.
- **A hold is green, not pending** (ADR 0005). The gate is a required check, so a
  `pending` hold made held PRs unmergeable even for the developer. Never change a hold
  back to `pending` or `failure`. Only a `pass` may enable auto-merge; that, not the
  status colour, is what keeps held PRs from merging on their own.
- **Test deletions are authorised only by the base branch's ticket** (ADR 0005). The
  gate reads `Deletes tests:` from the default branch's copy, never the PR's, so a
  worker cannot authorise its own deletion. Keep it that way.
- **A Jules session can stop to ask a question** (`AWAITING_USER_FEEDBACK`) even
  with plan approval off and a prompt that says nobody is watching. The engine
  answers it (ADR 0004): `max_auto_replies` auto-replies, then an escalation on the
  claim branch. Never "fix" this by treating a waiting session as dead. That releases
  its claim and starts the ticket a second time. Every message the engine sends
  starts with `AUTO_REPLY_MARKER` or `STOP_MARKER`, because those markers are the
  only record of what was sent. Keep them.
- **Jules's CI Fixer may also react to red CI.** The dispatcher's three-attempt
  count is the authority either way.
- **`.claude/` is gitignored.** Nothing a worker or reviewer needs may live there.
  Tickets, specs and escalation briefs go in `.scratch/`.
- **`ready-for-developer` near the root of a graph idles the repo** (ADR 0003).
  That is a planning problem, not a dispatcher bug. See §10.

---

## 8. Where the prior reasoning is written down

- `CONTEXT.md`: the glossary. Use its words exactly. If a term and the code
  disagree, flag it; do not silently pick a side.
- `docs/adr/0001` (auto-merge behind the integrity gate), `0002` (everything
  public; privacy by checks), `0003` (held tickets block dependents), `0004` (the
  engine answers a waiting worker), `0005` (landable tickets, authorised test
  deletions, green holds). Binding.
- `.scratch/phase-1/spec.md`: the Phase 1 spec. Its *Implementation Decisions*
  and *Testing Decisions* sections are the design of every module above.
- `docs/agents/issue-tracker.md`: ticket format and status vocabulary.
- `docs/agents/domain.md`: how skills consume the glossary and ADRs.

The Cowork project the planner works in is **not** readable by every tool that
works this repo. Anything an implementer must obey has to be here, in an ADR, or
in the ticket.

---

## 9. Implementing a ticket in this repo

Follow `src/ticket_engine/resources/ticket_skill.md` end to end. It is the same
skill every worker in every target repo receives. In short:

1. Read this file, the ticket, every ADR it names, `CONTEXT.md`, and the
   docstring of every module you will edit.
2. Work the frontier. Never start a ticket whose `Blocked by:` names a ticket that
   is not `done`. Never claim a `ready-for-developer` ticket.
3. Tests first, from the acceptance criteria. Watch them fail. Then implement.
4. Run the three gates in §1. **Zero failures before you push.**
5. **A failing test is fixed or escalated, never muted.** No `skip`, `xfail`,
   deleted or weakened assertion, loosened tolerance, narrowed input, or
   `try/except` swallowing the error.
6. When done: `Status: done`, every criterion ticked `- [x]`, a dated summary under
   `## Comments`, all in the same PR.
7. When you cannot finish honestly: `Status: blocked` and the escalation brief
   (format in the skill, §6) under `## Comments`. The brief goes in the ticket
   file, never under `.claude/`.

`scripts/check_tests_first.py` fails any change to `src/` that does not also touch
`tests/`. The escape hatches (`[no-test-needed: <reason>]`, the `tests-exempt`
label) exist, are visible in history, and in a target repo force a merge hold.

---

## 10. Planning in this repo

For the planner session. None of this is for a worker.

- **Route before planning.** Unsettled design → grill. Settled, one sitting → a
  plan. Settled with seams → spec, then tickets.
- **Every ticket must be landable through the integrity gate** (ADR 0005). The gate
  judges each PR unattended, so a ticket that asks for something the gate refuses can
  never land, however well it is implemented.

  1. **Never ask a worker to delete a test function** unless the ticket has a
     `**Deletes tests:** <file>.py::<test>, ...` line listing each one. Prefer
     rewriting a superseded test in place (same name, asserting the new behaviour),
     which needs no line. Never ask a test file to lose assertions, except inside
     the listed tests.
  2. **A ticket that changes `.github/`, a gate script, `docs/adr/`, `AGENTS.md` or
     `CONTEXT.md` is always held.** Mark it `Auto-merge: no` so it says so, and place it
     as a leaf.
  3. **Every ticket has `- [ ]` acceptance criteria.** Check 6 verifies them.
  4. **The gate list in a ticket matches the repo's CI**, command for command.
  5. **Run `dispatch --dry-run <path-to-target-clone>` and clear every
     "Ticket problems" line before handing off.** The dispatcher will not start a
     ticket that has one.
- **Number from the highest existing ticket** (§7). Check every effort directory,
  not just the one you are writing into.
- **Place `ready-for-developer` tickets as leaves** wherever the design allows
  (ADR 0003). A developer ticket at the root stalls everything behind it.
- **A `ready-for-developer` ticket's `Blocked by:` must be honest.** If the
  developer cannot act until agent work lands, list that work. The run report
  shows it as "waiting on", so the developer is not sent to do something early.
- **Acceptance criteria name their proof** in observable terms: the rendered report
  line, the action returned by `DispatchCore.evaluate`, the verdict and its reason
  text, the file `adopt` writes. Point at the code to copy by file and function.
- **Say what tests may fake** (the GitHub and Jules clients, `agy`, the clock) and
  what must be real (the parser, the core under change, a temp copy of any fixture
  repo).
- **A verification step that depends on a target repo seeing the change** must say
  the developer moves `v1` first, and the ticket that owns that step is
  `ready-for-developer`.
- **Roles, never names**, anywhere in a ticket or spec.

<!-- ACTIVE-PLAN:START -->
## Active implementation plan

This is a **pointer**, not the work. The work is a ticket set.

- Spec: `.scratch/needs-you-waiting-on/spec.md`
- Tickets: `.scratch/needs-you-waiting-on/issues/` — 16 → 17 → 18
- Next: **16** (run report Waiting on column). 17 is blocked by 16. 18 is
  `ready-for-developer` (move `v1`, confirm on Slackbot).

Phase 1 (`.scratch/phase-1/`) is complete except its developer tickets 13–15.
<!-- ACTIVE-PLAN:END -->

## Implementation Protocol

<!-- ticket-engine-bootstrap: implementation-protocol -->

Tickets are implemented following the engine's runner-agnostic skill.
See `src/ticket_engine/resources/ticket_skill.md` in `ilegault/ticket-engine`.

Key rules for all workers:
- Write tests from the acceptance criteria **before** any implementation.
- Cores are pure: no I/O in dispatch, integrity, or bootstrap cores.
- Every tunable lives in `.ticket-engine.toml`, never hardcoded in logic.
- Use `logging.getLogger(__name__)`; never `print()` in library code.
- One ticket, one branch, one PR. Never commit to the default branch.

## Roles, Not People

<!-- ticket-engine-bootstrap: roles-not-people -->

Never write real people's names, Slack IDs, or emails in code, commits,
tickets, or documentation. Refer to **roles** ("the approver", "a buyer",
"the developer"). See `ilegault/ticket-engine` ADR 0002.
