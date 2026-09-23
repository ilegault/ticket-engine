# CONTEXT.md — the vocabulary of ticket-engine

ticket-engine is the machinery that takes a planned ticket set in one of Isaac's
repos and gets it implemented by agents: overnight, in dependency order, merged
only when the tests pass honestly. These are the words it uses. If a term here
and the code disagree, **flag it; do not silently pick a side.** The decisions
behind these terms are in `docs/adr/`.

---

## Repos and roles

**Engine** — this repo, `ticket-engine`. Holds the shared machinery. Never holds
application code, tickets for other repos, or secrets.

**Target repo** — a repo the engine works on (Slackbot, TDS-T8, RBL, any future
one). It holds its own code, its own `AGENTS.md`, `CONTEXT.md`, ADRs and tickets.
It calls the engine; the engine never copies itself into it beyond a thin caller.

**Planner** — the Claude session (Cowork or Claude Code) that grills a design and
writes the spec and tickets. The planner never implements.

**Developer** — Isaac. The only one who approves a merge hold, resumes a paused
repo, or does a bench task.

---

## Tickets

**Ticket** — one markdown file at `.scratch/<effort>/issues/NN-<slug>.md` in a
target repo. The unit of work: one ticket, one branch, one pull request.

**Effort** — a directory under `.scratch/` holding one spec and its tickets.

**Status** — the ticket's `Status:` line. Exactly one of five words, read
mechanically, so no synonyms:

| Status | Meaning |
|---|---|
| `ready-for-agent` | may be claimed by a worker |
| `ready-for-developer` | needs the developer (bench work, a judgement call). A worker must never claim it |
| `in-progress` | a worker is on it (only visible on the ticket branch; see *Claim*) |
| `blocked` | escalated; the reason is under the ticket's `## Comments` |
| `done` | merged |

`complete`, `completed` and `human-task` are **not** statuses. They are legacy
spellings and are migrated away.

**Blocked by** — the ticket's `Blocked by:` line. The tickets that must be `done`
before this one can start.

**Runner** — the ticket's `Runner:` line: `any` (default) or `windows`. A
`windows` ticket needs a Windows machine to verify honestly, so a Linux worker
never takes it.

**Auto-merge** — the ticket's `Auto-merge:` line: `yes` (default) or `no`. The
planner sets `no` on safety-critical tickets. A `no` ticket always ends in a
*merge hold*.

**Frontier** — the tickets that are `ready-for-agent` and whose every *Blocked
by* ticket is `done`. First by number wins. The frontier is computed from
`master`/`main`, never from a branch.

---

## Workers and dispatch

**Worker** — anything that implements a ticket. Two kinds exist:
- **Jules worker** — a Jules session in Google's cloud. Linux. Takes `Runner: any`.
- **Local worker** — the Antigravity CLI (`agy`) on one of the developer's
  machines, started by the engine's local command. Takes `windows` tickets when
  run on Windows. (Phase 2 makes the ProDesk a permanent local worker.)

**Dispatcher** — the part of the engine that looks at a target repo, computes the
frontier, and starts workers on it. It keeps **no state of its own**: everything
it knows it reads from GitHub and the Jules API on each run.

**Claim** — a branch named `claim/<effort>/<NN>` in the target repo, created
through the GitHub API before any work starts. Creation fails if it already
exists, so two workers cannot claim one ticket. Deleted when the ticket's PR
merges or the claim is released.

**Stale claim** — a claim whose worker has shown no progress (no live Jules
session, or no checkpoint commit) for 12 hours. The dispatcher releases it.

**Checkpoint** — a local worker's work-in-progress commit pushed to the ticket
branch, plus a progress note of five lines or fewer under `## Comments`. What a
resumed worker continues from. Jules workers do not checkpoint: a running Jules
session is never cut off by quota.

**Quota reserve** — the headroom the dispatcher keeps. Jules: never start a
session when fewer than 10 of the rolling-24-hour allowance remain. Local worker:
never start a ticket below 20% remaining; pause (not abandon) when quota runs out.

**Daily cap** — the most tickets the dispatcher will start in one target repo in
24 hours. A blast-radius limit, not a quota limit. Configured per repo.

---

## Outcomes

**Fix attempt** — one red CI run on a ticket's PR followed by a new push. Red CI
is normal and is not an escalation.

**Escalation** — the worker gives up: three fix attempts have failed, or the
tests cannot pass without muting or weakening them. The ticket goes `blocked`, the
PR goes to draft, and an **escalation brief** is written under `## Comments`: the
ticket, what each attempt tried, the exact failing output, and the one decision
needed, ready to paste into a stronger model.

**Integrity gate** — the fifth CI check, after the target repo's own gates. It
decides whether a green PR may merge on its own. Its answer is a *verdict*.

**Verdict** — `pass` (auto-merge), `fail` (red check, the worker must fix), or
`hold` (green, but waits for the developer). Always with reasons.

**Merge hold** — a PR whose verdict is `hold`. It is never auto-merged. Tickets
that depend on it wait; nothing is built on top of an unmerged PR.

**Circuit breaker** — two escalations in one target repo within 24 hours pause
that repo's dispatch until the developer resumes it.

**Paused** — a target repo whose dispatch is stopped (by the circuit breaker or by
hand). Work already in flight finishes; nothing new starts.

**Morning report** — one issue in the engine repo, rewritten daily: what merged,
what escalated, what is held, what waits for a Windows worker, which repos are
paused, and quota standing.

---

## Setup

**Bootstrap** — the skill that wires a repo to the engine. Two modes:
**new** (a fresh repo) and **adopt** (an existing repo; migrates legacy statuses
and ticket skills).

**Secrets file** — `secrets.env` on the developer's machine, outside every repo.
The bootstrap reads it and writes each value into the target repo's GitHub
secrets. The required keys are `JULES_API_KEY`, `PIPELINE_TOKEN`, and
`PEOPLE_DENYLIST`.

**People denylist** — real names, Slack IDs and emails that must never appear in a
public file. Stored only as a GitHub secret; never committed. Tickets and docs
refer to **roles** ("the approver", "a buyer"), never to people.
