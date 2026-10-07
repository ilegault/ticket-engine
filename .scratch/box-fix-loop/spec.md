# Spec: the box fixes red CI or escalates, behind one gate list and named tests

**Status:** ready-for-agent
**Binding:** ADR 0011, ADR 0012, ADR 0013 (and ADR 0001, 0002, 0005, 0006, 0007, 0008, 0010 where they touch the same code)
**Glossary:** *Fix attempt*, *Fix prompt*, *Pre-push gate*, *Gate*, *Named test*, *Box launcher*, *Box status issue*, *Ticket lint* (CONTEXT.md)

## Problem Statement

The developer runs the box overnight so tickets land without him. On 2026-10-07 a
box PR went red on lint (two unused imports in a new test file) and sat for hours.
Tests and the integrity gate were green, auto-merge was on, and the box status
issue kept saying `working`. He could not tell whether agy was fixing it or had
given up. It had given up, silently:

- The box counted fix attempts only in memory, counted runs that pushed nothing,
  and stopped at three without escalating. The dispatcher's red-CI escalation it
  relied on never runs: the live dispatcher builds its snapshot without open PRs.
- The fix run reused the implement-from-scratch prompt (check the frontier, claim,
  set `in-progress`) on a ticket already `done` and claimed, and named only the
  failing check (`- lint`), not its output.
- A fix run that failed or timed out was dropped instead of resumed or escalated.
- Lint reached CI at all because nothing on the box ran the repo's checks before
  pushing; the engine trusted the worker's own local gate.

Behind that, two weaknesses he cares about more than any single PR:

- **A ticket can land hollow.** Check 6 only asks that the worker ticked every box,
  and check 7 is satisfied by one trivial new test. A worker once marked a ticket
  done having built almost none of it.
- **The checks can drift.** A repo's CI runs one list of commands and the engine
  knows a different one (`gate_commands`). Slackbot's CI runs a type gate the
  engine's default list does not.

Smaller pains: every engine fix reaches the box only when he is physically at it
to pull and restart; the status issue is terse and in UTC; and there is no way to
keep Jules off a repo while its CI handling is broken too.

## Solution

The box finishes what it starts or says clearly that it could not:

- Before anything is pushed, the box runs the repo's gate list and the integrity
  gate locally. A red result never reaches GitHub; agy is sent back with every
  failure at once.
- If CI still goes red, the box sends agy back with a fix prompt that says exactly
  what the situation is and carries the failing output. Up to three fix attempts,
  counted durably, then a proper escalation: brief, draft PR, label, issue.
- Each repo has one gate list. CI runs it through the engine's shared `gate`
  workflow, the box runs it as the baseline and the pre-push gate. Required checks
  are exactly `gate` and the integrity gate.
- Every acceptance criterion names the test that proves it, and the integrity
  gate refuses a PR unless every named test exists and failed on the old code.
- The box updates its own engine code between tickets and rolls back an update
  that keeps it from starting.
- The status issue says what the box is doing step by step, in Central time.
- A repo can be kept away from Jules with the existing `jules_enabled = false`.

## User Stories

1. As the developer, I want the box to notice red CI on its own PR within one tick, so that a red PR never sits unattended.
2. As the developer, I want the box to send the worker back to a red PR with the failing output, not just the check's name, so that the worker can actually fix it.
3. As the developer, I want the fix prompt to say the ticket is already the worker's and already `done`, so that the worker does not stop because the ticket looks claimed or finished.
4. As the developer, I want the fix prompt to forbid claiming, changing the ticket's status, or muting or weakening tests, so that a fix cannot cheat its way to green.
5. As the developer, I want the fix prompt to carry the local gate output for the repo's own commands, so that the worker sees exactly what ruff, the type gate or the tests said.
6. As the developer, I want the fix prompt to carry the tail of the GitHub job log for checks the box cannot reproduce, so that an integrity `fail` is fixable too.
7. As the developer, I want every fix run to count as a fix attempt whether or not it pushed, so that a worker that does nothing cannot loop forever.
8. As the developer, I want fix attempts counted in a file that survives a box restart, so that a restart neither forgets the count nor grants three fresh attempts.
9. As the developer, I want the box to escalate a ticket once its fix attempts run out, so that I hear about a stuck ticket instead of discovering it.
10. As the developer, I want a fix-attempt escalation to do exactly what a resumes-exhausted escalation does — brief in the ticket, `blocked` status, draft PR, `engine:escalated` label, one escalation issue — so that every escalation looks the same.
11. As the developer, I want the escalation brief to contain the failing output of the last attempt, so that I can paste it into a stronger model and decide.
12. As the developer, I want a fix run that ends in quota, timeout, failure or waiting to be handled exactly like an implement run, so that fix runs are never silently dropped.
13. As the developer, I want a quota pause during a fix run never to count as a fix attempt, so that quota does not cause escalations.
14. As the developer, I want the box to run the repo's gate list on the worktree after every worker run, before anything is pushed, so that lint, type and tests-first failures never reach GitHub.
15. As the developer, I want the pre-push gate to run every command even after one fails, so that the worker sees all failures in one go.
16. As the developer, I want the pre-push gate to finish with the integrity gate run locally against the default branch, so that an integrity `fail` is caught before the push too.
17. As the developer, I want the local integrity run to write nothing to GitHub, so that no comment, status or label appears for a run that was never pushed.
18. As the developer, I want a local integrity `hold` not to block the push, so that tickets that are meant to wait for me still reach a PR.
19. As the developer, I want a red pre-push gate to send the worker back with every failing output and count as a resume, so that the existing resume budget bounds it and exhausting it escalates.
20. As the developer, I want the pre-push gate's output kept in the box log and the prompt only, never in a GitHub request body, so that the engine's privacy rules hold.
21. As the developer, I want the pre-push gate to run in the repo environment with the repo's `[test_env]`, so that it sees the same packages and variables CI does.
22. As the developer, I want each repo's `gate_commands` to be the only list of its own checks, so that the box and CI can never disagree about what must pass.
23. As the developer, I want CI to run that list through one shared engine workflow, called from each target repo in a few lines, so that adding a check means editing one list.
24. As the developer, I want the shared `gate` workflow to set up `python_version`, run `install` and set `[test_env]` from `.ticket-engine.toml`, so that CI builds the same environment the box does.
25. As the developer, I want the `gate` workflow to run every command and report each one's pass or fail, so that a failing ruff never hides a failing type gate.
26. As the developer, I want the baseline to use the same gate runner as the pre-push gate and CI, so that all three behave identically.
27. As the developer, I want exactly two required checks, `gate` and the integrity gate, so that nothing the box cannot see can block a merge.
28. As the developer, I want `add-repo` to create a ruleset requiring both checks, so that a newly added repo is protected the same way.
29. As the developer, I want `add-repo`'s adopt to write the `gate` workflow call into a repo, so that every repo I add (RBL next) gets the single gate list.
30. As the developer, I want `add-repo --check` to warn about workflows other than the engine's, so that I notice a check that still lives outside the gate list.
31. As the developer, I want `add-repo --check` to list open `ready-for-agent` tickets that fail ticket lint, so that the ticket rewrite happens before a repo is switched on.
32. As the developer, I want every test named in a ticket's acceptance criteria to be required to exist in the PR, so that a ticked box is backed by a test.
33. As the developer, I want every named test to be required to have failed on the base code, so that the test proves the behaviour was built rather than already there.
34. As the developer, I want a criterion that rewrites an existing test to require only that the test exists and passes, so that rewriting a test in place stays possible.
35. As the developer, I want a missing or never-failing named test to be a `fail`, so that the worker must fix it rather than me reviewing it.
36. As the developer, I want named tests matched by name anywhere under the repo's test paths, so that check 9 is not brittle about which file a test landed in.
37. As the developer, I want ticket lint to refuse a `ready-for-agent` ticket whose criterion names no test, so that the planner cannot write a criterion an agent can fake.
38. As the developer, I want a criterion tagged `(by hand)` or `(no test: <reason>)` to be exempt from that rule, so that doc-only and "existing tests unchanged" criteria still work and every exception is visible.
39. As the planner, I want ticket lint's finding to name the ticket and the criterion with no named test, so that I can fix it in one pass.
40. As the developer, I want no grandfathering: every open `ready-for-agent` ticket in every target repo rewritten before the lint rule ships, so that the hollow-ticket loophole closes everywhere at once.
41. As the developer, I want check 9 to reach a target repo only when I move the `v1` tag, so that I control when the stricter gate starts applying there.
42. As the developer, I want the box to update its own engine checkout when the engine's default branch moves, so that engine fixes reach the box without me being there.
43. As the developer, I want the box to update only when no worker run is in progress, so that an update never interrupts agy mid-ticket.
44. As the developer, I want the box's scheduled task to run a launcher that never imports the code being updated, so that a broken update cannot break the rollback.
45. As the developer, I want the launcher to roll back to the last engine commit the box ran cleanly on after three failed starts within 15 minutes of an update, so that a bad merge costs minutes, not a night.
46. As the developer, I want a rollback to open one box alert naming the bad commit, so that I know an engine merge broke the box.
47. As the developer, I want the box not to update again until the engine's default branch moves past a rolled-back commit, so that it does not loop on the same bad update.
48. As the developer, I want the status issue to show the box's current step — implementing, pre-push gate red with the resume count, fixing CI with the attempt count, or waiting for quota — so that I can tell at a glance whether it is working or stuck.
49. As the developer, I want the status issue to show the last finished ticket and its PR, so that I can see what the box most recently delivered.
50. As the developer, I want the status issue to show today's starts per repo against each daily cap, so that I can see how much room is left.
51. As the developer, I want the status issue to show the engine commit the box runs and when it last updated, so that I know whether a fix has reached the box.
52. As the developer, I want every time in engine text I read — status issue, morning report, box alerts, escalation issues and briefs, run report, box log lines — in Central time with CDT or CST, so that I never convert from UTC.
53. As the developer, I want the timezone set by one config key that switches daylight saving on its own, so that the times stay right all year.
54. As the dispatcher, I want to read the box status issue's times in its new format, so that overflow and box-silent decisions still work.
55. As the developer, I want files the engine reads back (ledgers, readiness, the fix-attempt ledger) to stay in UTC, so that machine state is unambiguous.
56. As the developer, I want the dispatcher to honour `jules_enabled = false` in a repo's `.ticket-engine.toml`, so that I can keep Jules off a repo while its red-CI handling is unfinished.
57. As the developer, I want the ticket skill to stop promising Jules the failing CI output, so that no worker is told something that does not happen.
58. As the developer, I want the dispatcher's own PR-escalation path documented as Jules-only and unwired, so that nobody mistakes it for the box's safety net again.
59. As the developer, I want this effort to land before add-repo tickets 61 and 62, with 62 rewritten to use the shared gate runner, so that the box and the baseline never edit the same code in parallel.
60. As the developer, I want Slackbot and TDS-T8 moved onto the `gate` workflow with their full CI command lists as `gate_commands`, so that nothing their CI runs today slips through the pre-push gate.

## Implementation Decisions

**Fix attempts and escalation (box core and box loop)**
- The box's pure next-step logic keeps choosing a fix step for a box-claimed ticket whose open PR has failed checks and whose fix-attempt count is below the repo's `max_fix_attempts`. When the count has reached it and the ticket is not yet `blocked`, it chooses a new escalate step for that ticket, ahead of claiming anything new.
- The fix-attempt count lives in a box-local ledger file in `logs_dir`, keyed by repo and ticket number, written before each fix run starts. The in-memory counter is removed. Losing the file only resets counts (same tolerance as the starts ledger).
- A fix attempt is recorded when a fix run starts, whatever its outcome — except that a run ending in `quota` is not counted.
- The escalate step reuses the local worker's existing escalation (brief written into the ticket, `blocked`, commit, push, draft PR or convert to draft, `engine:escalated` label, one escalation issue). Its reason is the existing `ci_failed` escalation reason. The brief's failing-output section carries the last attempt's failing output.

**Fix runs (local worker)**
- A fix run builds a **fix prompt** with a new pure prompt function beside the existing prompt assembly. It contains: the repo and ticket path; that the ticket is already claimed by this worker and already `done`; that the worker must not run the claim check, claim, or change `Status:`; that it fixes only what is listed and commits; the existing unattended rule and the no-muting/no-weakening rules from the ticket skill; and one section per failing check with its output.
- Failing output comes from the gate runner run locally in the worktree for the repo's own commands, plus the tail of the GitHub job log for each failing check the box cannot reproduce (fetched through the GitHub client with the box's token). Output is truncated to a fixed tail length per check.
- Fix runs go through the same outcome handling as implement runs (quota returned to the box loop in box mode, `waiting` auto-replies, `timeout`/`failed` resumes, escalation when exhausted). The fix run passes through the pre-push gate before pushing, like any run.

**Pre-push gate (local worker)**
- Before the box opens a PR, and before every push to a ticket branch that already has an open PR, the local worker runs the gate runner over `gate_commands` in the worktree with the repo environment and `[test_env]`, then the integrity gate locally against the default branch.
- The local integrity run uses the integrity runner's command-line entry with a mode that performs no GitHub writes (no PR comment, no commit status, no labels).
- Any red command or an integrity `fail`: no push; the worker is run again on the same worktree with a prompt section listing every failure's output; this counts as one resume against `max_resumes_per_ticket`. An integrity `hold` or `pass` with all commands green: push and continue as today.
- Gate output is logged to the box log (tail only) and placed in prompts; it never enters a GitHub request body.

**One gate runner**
- A new module owns running a gate list: input is the command list, working folder, environment variables, a timeout and an injected command runner; output is a result listing each command with pass/fail, exit code and output tail, plus overall pass/fail. It always runs every command.
- The baseline (ticket 62), the pre-push gate and CI all use it. CI reaches it through a new engine console command, `engine-gate`, which reads `gate_commands` and `[test_env]` from the current repo's `.ticket-engine.toml`, runs the gate runner, prints a per-command report, and exits non-zero if any command failed.

**The shared `gate` workflow**
- A new reusable workflow in the engine, published at the same `v1` tag as the integrity workflow. It checks out the caller, reads `python_version` and `install` from `.ticket-engine.toml`, sets up that Python, runs `install`, installs the engine, and runs `engine-gate`. Its job is named `gate`.
- Target repos call it from one small workflow on `pull_request` and `push`. Their own lint/test workflows are removed (by retrofit for existing repos, by add-repo adopt for new ones).

**add-repo**
- Adopt (and upgrade) writes the `gate` workflow call into the target repo.
- The ruleset add-repo creates requires both `gate` and the integrity gate.
- `--check` warns about any workflow in the target besides the engine's gate, integrity and dispatch callers. Open tickets that fail ticket lint already show up in the dispatcher dry run add-repo runs after the adopt merge (ticket 66).

**Integrity check 9 and ticket lint**
- Integrity core gains check 9. Inputs: the PR's ticket acceptance criteria, the set of test function names present under `test_paths` on the head, and check 7's set of new tests that failed on base. Every backticked `test_` name in a criterion must be present on the head; and must be in check 7's failed-on-base set, unless that criterion contains the word `rewrite` (or `rewrites`/`rewritten`) naming an existing test, in which case it must only be present. Violations are a `fail`, one reason per test naming the criterion. Check 9 is silent when the ticket names no tests. Check 9 runs only when check 6 found a single `done` ticket.
- Ticket lint gains a finding for each unchecked criterion of a `ready-for-agent` ticket that contains no backticked `test_` name and no `(by hand)` or `(no test: …)` tag.

**Self-updating box and launcher**
- The box loop, when no worker run is in progress, compares its checkout's commit with the engine default branch's head (GitHub API). If different and not the recorded bad commit, it pulls `--ff-only`, reinstalls the engine, records the update, and exits cleanly.
- A new `box-launcher` console command is what the scheduled task runs. Its decision is a pure function of a small state record (last good commit, last update time, recent start failures, bad commit) and the current time, returning start, roll back to a commit, or hold. A thin wrapper performs the process start, git checkout and reinstall, and writes the state. The box worker marks a commit "good" after its first completed tick.
- Rollback opens one box alert with the new kind *engine update rolled back*, naming the bad commit; the alert closes when a later update succeeds.

**Status issue and Central time**
- The status record gains: current step (implementing / pre-push gate red with resume n of m / fixing CI with attempt n of m / waiting for quota), last finished ticket with its PR number and merge time, today's starts per repo with each daily cap, engine commit and last update time. Times render as `YYYY-MM-DD h:mm AM/PM CDT|CST`.
- The parser reads the new layout, including its times; the dispatcher and morning report keep working against it. It still never contains free text from tests or agy (fixed template, ADR 0007).
- A new engine config key `display_timezone` (default `America/Chicago`) controls every human-facing time: status issue, morning report, box alerts, escalation issues and briefs, run report, and box log line timestamps. Machine files stay UTC.

**Jules**
- The dispatcher's pure core already starts no Jules session for a repo whose `.ticket-engine.toml` has `jules_enabled = false` (ADR 0010 rule 1); no change is needed.
- The ticket skill's Jules section no longer says the worker will receive the failing CI output.
- The dispatcher's PR-escalation code gets a docstring note that it is Jules-only and not wired into live dispatch.

**Ordering with add-repo**
- The gate runner lands first in this effort. Add-repo ticket 62 is rewritten to use it for the baseline, and 61 and 62 are blocked by this effort's box-loop tickets so the two efforts never edit the box loop in parallel.
- A planner-only ticket (`ready-for-developer`) rewrites every open `ready-for-agent` ticket in Slackbot, TDS-T8 and ticket-engine to name a test per criterion; the ticket-lint rule is blocked by it.
- Add-repo ticket 68 (retrofit Slackbot and TDS-T8) gains: move to the `gate` workflow, set `install` and full `gate_commands` mirroring today's CI command for command, delete the old test workflows, and replace hand-set required checks with `gate` plus integrity. RBL goes through add-repo after this effort.

## Testing Decisions

- Good tests here assert what the box, the gate or the worker *does* — the next step chosen, the prompt text sent, whether a push happened, the escalation's effects, the verdict and its reasons, the rendered issue body — never how a function is structured internally. Fakes stand in only for agy, git, GitHub and subprocesses; the logic under change runs for real. Every new test is written first and watched failing.
- **Box core** (pure, `BoxWorld` snapshots; prior art: the existing box core tests): fix attempts below budget → fix step; at budget → escalate step before any claim; quota never increments; the status step label for each state.
- **Local worker** (fake agy driver, fake git runner, fake command runner, mock GitHub client; prior art: the existing local worker tests for tickets 28–30 and 47): the fix prompt contains the "already yours and done" text, each failing check with its output, and no passing check; a red pre-push gate means no push and a second agy run whose prompt holds every failure; a green gate pushes; a local integrity `hold` still pushes; fix runs that fail are resumed and then escalated with all five effects; gate output never appears in a GitHub request body (copy the existing "agy failure text never reaches a GitHub request body" test).
- **Box loop** (`make_loop`, `make_github`; prior art: the existing box worker tests): the fix-attempt count survives a second loop built on the same `logs_dir`; the loop updates only when no run is in progress and exits after updating; the status issue body carries the new fields.
- **Gate runner** (injected command runner): every command runs even after a failure; results keep order, exit codes and output tails; a timeout counts as red. The `engine-gate` command is tested through its entry function with a real `.ticket-engine.toml` in a temporary folder.
- **Integrity core** (pure; prior art: the existing integrity core and check 7 tests): named test missing → fail naming it; named test present but passed on base → fail; rewrite criterion with existing passing test → no failure; no named tests → silent.
- **Ticket lint** (prior art: the existing ticket quality tests): criterion without a named test → finding; `(by hand)` and `(no test: …)` → no finding.
- **Status issue** (prior art: the existing box status and morning report tests): render then parse returns the same record, in CDT and in CST (one date each side of the daylight-saving switch); the morning report still reads the box.
- **Launcher decision** (pure): three failed starts within 15 minutes of an update → roll back to the last good commit; failures outside the window → start; a recorded bad commit → hold until the default branch moves past it.
- **Dispatch core** (prior art: the existing dispatch core tests): `jules_enabled = false` → no Jules start for that repo, stale-claim release still happens.
- **add-repo** (prior art: the existing add-repo tests with the recording fake `gh`): adopt writes the gate workflow call; the ruleset JSON requires both contexts; `--check` prints both warnings.
- **Workflow files** (prior art: the bootstrap adopt tests that read workflow files): the `gate` workflow reads `.ticket-engine.toml` and runs `engine-gate`; its job is named `gate`.
- **By hand only:** the box's scheduled task running `box-launcher`, a real rollback on the box, and a target repo's real ruleset — covered by the box runbook and ticket 68.

## Out of Scope

- Red-CI handling for Jules PRs (feeding Jules its CI output, counting, escalating). Jules can be kept off a repo with `jules_enabled = false` meanwhile.
- Wiring or deleting the dispatcher's PR-escalation path.
- The engine ever editing code itself (no automatic `ruff --fix`).
- Running several agy sessions on the box at once.
- Automatically moving the `v1` tag; the developer moves it.
- Automatically rewriting an existing repo's CI into the gate list beyond what adopt writes; existing repos move by retrofit.
- A drift check between CI and `gate_commands` (unnecessary with one list).
- Rewriting the open tickets themselves — that is the planner-only ticket, not agent work.

## Further Notes

- Unblocking PR #125 (Slackbot ticket 101) is a hand fix: delete the two unused names from its test file's `src` import. Tonight's Jules stopgap is disabling Slackbot's `dispatch` workflow in the Actions tab.
- Tickets that change `.github/`, gate scripts, `docs/adr/`, `AGENTS.md` or `CONTEXT.md` are held (`Auto-merge: no`) and placed as leaves. The `gate` workflow and the ticket-skill text change fall under this.
- Engine changes reach the box by hand until the self-update ticket lands; that ticket should come early so later ones arrive automatically.
- ADRs 0011–0013 and the CONTEXT.md glossary changes were written during grilling and are uncommitted in the developer's checkout.
