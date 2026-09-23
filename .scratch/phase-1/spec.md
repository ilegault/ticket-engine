# Spec: ticket-engine Phase 1 — tickets implemented overnight by Jules, merged only when honest

**Status:** ready-for-agent

Vocabulary is `CONTEXT.md`. Binding decisions are `docs/adr/0001`–`0003`.

## Problem Statement

The developer plans work carefully in Claude (grill → spec → tickets) across three
public repos: Slackbot (P-Bot), TDS-T8 and RBL. Implementation is the bottleneck.
Today a ticket moves only when the developer starts an implementing session by
hand, watches it, and merges the result. A ticket set of ten takes ten sittings.
Claude usage that should go to planning goes to implementation instead, and
nothing happens while the developer sleeps.

The developer has a Google AI Pro plan that includes Jules (100 cloud agent
sessions per rolling day, far more than needed), and CI with honest tests in every
repo. What is missing is the part that connects them: something that knows which
ticket is next, hands it to an agent, and merges the result only when the tests
pass honestly. That part also needs to be safe to leave alone, because an
unattended agent's cheapest route to a green build is to weaken the tests.

The machinery that does exist has drifted across repos. Each repo has its own
copy of the tests-first gate (all three differ) and its own ticket skill (155 to
331 lines). The status words differ: RBL says `ready-for-developer`, Slackbot says
`human-task`, and old tickets say `complete` and `completed`. The ticket skills
assume PowerShell and the `gh` CLI, which a cloud agent does not have.

## Solution

A new public repo, **ticket-engine**, that every target repo calls.

After the developer pushes a ticket set to a target repo, the **dispatcher** runs
on every push to the default branch and on an hourly timer. It computes the
**frontier**, **claims** up to two tickets per repo, and starts a **Jules worker**
on each with a prompt that carries the runner-agnostic ticket skill. Jules
implements, runs the gate, and opens a PR. CI runs the repo's own gates and then
the **integrity gate**. A `pass` verdict auto-merges, and that merge wakes the
dispatcher for the next ticket. A `hold` waits for the developer. Red CI gets
three **fix attempts**, then an **escalation** with a brief ready to paste into a
stronger model. Two escalations in a day trip the **circuit breaker** for that
repo. The dispatcher counts Jules sessions itself and keeps a **quota reserve**.

`Runner: windows` tickets are skipped by the Jules dispatcher, listed in the
**morning report**, and run on demand on the developer's PC by one local command
that drives the Antigravity CLI. In Phase 2 the same command runs on a Windows
mini PC.

A **bootstrap** skill wires any repo to the engine: `adopt` for the three existing
repos (migrating legacy statuses and skills), `new` for future ones. It reads
secrets from a local `secrets.env` and writes them into GitHub secrets.

Rollout: Slackbot first. TDS-T8 and RBL are adopted only after Slackbot has run a
real ticket set end to end.

## User Stories

### Planning and handoff
1. As the developer, I want pushing a ticket set to my repo to be the only action needed to start implementation, so that handoff costs me nothing.
2. As the planner, I want one ticket template with `Status`, `Blocked by`, `Runner` and `Auto-merge` lines, so that every ticket is machine-readable the same way in every repo.
3. As the planner, I want to mark a safety-critical ticket `Auto-merge: no`, so that it can never merge without the developer.
4. As the planner, I want to mark a ticket `Runner: windows`, so that no Linux worker verifies it dishonestly.
5. As the planner, I want written guidance to place approval-required tickets late in the dependency graph, so that few tickets ever wait behind a hold.
6. As the planner, I want the Cowork planning instructions to live in the engine as a template rendered per repo, so that every repo's planner follows the same rules.
7. As the developer, I want the five status words defined once in the engine, so that no repo drifts again.

### Dispatch
8. As the developer, I want the dispatcher to run on every push to the default branch, so that a merge immediately starts the next ticket.
9. As the developer, I want the dispatcher to also run hourly, so that the chain restarts itself after a quota pause or a failed run.
10. As the developer, I want the dispatcher to start only `ready-for-agent` tickets whose every blocker is `done`, so that work happens in dependency order.
11. As the developer, I want the dispatcher to read `**Status:**` in bold and plain `Status:` alike, so that existing tickets parse.
12. As the developer, I want the dispatcher to never claim a `ready-for-developer` ticket, so that bench work is never simulated.
13. As the developer, I want at most two tickets in flight per repo, so that parallel work rarely conflicts.
14. As the developer, I want all repos dispatched independently, so that one repo's trouble never stalls another.
15. As the developer, I want a claim to be a branch created through the GitHub API, so that two workers can never take the same ticket.
16. As the developer, I want a stale claim (no progress for 12 hours) released automatically, so that a dead worker does not strand a ticket.
17. As the developer, I want the dispatcher to count Jules sessions from the last 24 hours and keep a reserve of 10, so that it never runs my allowance dry.
18. As the developer, I want a per-repo daily cap, so that a misbehaving loop has a bounded blast radius.
19. As the developer, I want `Runner: windows` tickets skipped by the Jules dispatcher without blocking unrelated tickets, so that Linux-safe work keeps flowing.
20. As the developer, I want the dispatcher to keep no state of its own, so that there is nothing to corrupt or lose between runs.
21. As the developer, I want a paused repo to start nothing new while in-flight work finishes, so that pausing is safe.

### Implementation by Jules
22. As a Jules worker, I want the full ticket skill in my prompt, so that I follow the repo's conventions without reading a file I cannot see.
23. As a Jules worker, I want the prompt to name the exact ticket file and the repo's `AGENTS.md`, so that I start oriented.
24. As a Jules worker, I want the skill to tell me not to push or open PRs myself, so that I do not fight the automatic PR creation.
25. As the developer, I want each Jules session created with automatic PR creation and automatic plan approval, so that no step waits for a click.
26. As the developer, I want each target repo to have a Jules environment setup script that installs its Python version and system libraries, so that Jules can run the repo's gates.

### Merging
27. As the developer, I want a PR to auto-merge when CI is green and the verdict is `pass`, so that the chain moves unattended.
28. As the developer, I want the integrity gate to fail a PR that newly skips or xfails a test, so that muting is caught mechanically.
29. As the developer, I want it to fail a PR that deletes a test function or reduces a test file's assertions, so that weakening is caught.
30. As the developer, I want it to fail a PR that raises any ratchet, so that ratchets only go down.
31. As the developer, I want a PR that touches `.github/`, gate scripts, ADRs, `AGENTS.md` or `CONTEXT.md` held, so that no agent loosens its own rules.
32. As the developer, I want a PR that uses a tests-first escape hatch held, so that "visible in review" still means something.
33. As the developer, I want a PR whose ticket is not `done` with every box ticked to fail, so that half-finished tickets never merge.
34. As the developer, I want a PR whose new tests already pass on the base code to be flagged, so that tests that don't test the feature are caught.
35. As the developer, I want every PR scanned against a people denylist held only as a secret, so that names and IDs never reach a public repo.
36. As the developer, I want an `Auto-merge: no` ticket always held, so that my approval is never bypassed.
37. As the developer, I want the verdict's reasons posted on the PR, so that I can act on a hold in one look.

### Failure and escalation
38. As the developer, I want red CI to be answered by the worker fixing and pushing again, up to three times, so that ordinary failures never reach me.
39. As the developer, I want a ticket that still fails after three attempts escalated (ticket `blocked`, PR draft, label applied), so that it stops consuming quota.
40. As the developer, I want an escalation brief (ticket, each attempt, the exact failing output, the one decision needed) written under the ticket's `## Comments`, so that I can paste it straight into Opus.
41. As the developer, I want tickets depending on an escalated or held ticket to wait while independent tickets continue, so that one problem idles as little as possible.
42. As the developer, I want two escalations in one repo within 24 hours to pause that repo, so that a shared breakage does not burn a night of runs.
43. As the developer, I want to resume a paused repo with one action, so that recovery is trivial.

### Windows tickets and the local worker
44. As the developer, I want one local command that finds `windows` frontier tickets across my repos, claims one, and runs it with the Antigravity CLI, so that Windows work uses the same pipeline.
45. As the developer, I want the local worker to check Antigravity's remaining quota before starting and refuse below 20%, so that it does not start work it cannot finish.
46. As the developer, I want the local worker to checkpoint at acceptance-criterion boundaries with a note of five lines or fewer, so that a quota stop costs little and resuming is cheap.
47. As the developer, I want a quota-stopped local ticket to pause and resume (via `agy --continue`, or else a fresh session from the checkpoint), so that nothing restarts from zero.
48. As the developer, I want the local worker's PRs to go through the same integrity gate, so that there is one merge standard.

### Reporting
49. As the developer, I want a morning report issue in the engine repo listing merged, escalated, held, windows-waiting and paused items plus Jules quota standing, so that I know in one read what needs me.
50. As the developer, I want GitHub to email me when that issue changes, so that the report comes to me.

### Bootstrap
51. As the developer, I want `adopt` mode to migrate legacy statuses (`complete`, `completed`, `human-task`) to the five words, so that the frontier is readable.
52. As the developer, I want `adopt` to replace the repo's own ticket skill with the engine's runner-agnostic one, so that there is one skill.
53. As the developer, I want `adopt` to replace the repo's tests-first script with the engine's, so that the three copies stop drifting.
54. As the developer, I want the bootstrap to install the thin caller workflows (dispatch, integrity gate) pinned to an engine version, so that engine upgrades are deliberate.
55. As the developer, I want the bootstrap to read a local `secrets.env` and set `JULES_API_KEY`, `PIPELINE_TOKEN` and `PEOPLE_DENYLIST` as repo secrets, so that I never paste keys by hand.
56. As the developer, I want the bootstrap to turn on auto-merge, secret scanning with push protection, and a ruleset that requires all gates on the default branch, so that the repo is protected before the first dispatch.
57. As the developer, I want the bootstrap to print the Jules setup script and any steps it cannot automate, so that I finish setup in one pass.
58. As the developer, I want `new` mode to create skeleton `AGENTS.md`, `CONTEXT.md`, tests-first ADR, `.scratch/` layout and CI, so that a new project starts wired.
59. As the developer, I want the bootstrap to be re-runnable without clobbering my edits, so that I can use it to upgrade a repo.
60. As the developer, I want every existing real name or Slack ID in public files reported by `adopt`, so that I can replace them with roles.

## Implementation Decisions

**Language and shape.** The engine is Python (3.12+, matching Jules's VM), with
few dependencies. It has three cores and thin adapters:

- **Dispatch core**: a pure function from a *world snapshot* to a list of
  *actions*. The snapshot holds the repo config, the parsed tickets on the default
  branch, existing claim branches, open PRs with labels and CI history, Jules
  sessions with create times and states, escalations in the last 24 hours, and the
  paused flag. The actions are: create claim, start Jules session, release claim,
  escalate PR, pause repo, and report lines. It performs no I/O.
- **Integrity core**: a pure function from (base tree, PR diff, ticket file,
  config, test-run results) to a verdict with reasons. Running the new tests
  against base (check 7) happens in the adapter; the core only judges the results.
- **Bootstrap core**: from (mode, repo directory, config, secrets) to the files to
  write and the GitHub operations to perform.
- **Adapters**: a GitHub client (REST), a Jules client, a git/test runner, and an
  Antigravity CLI driver. Each is thin, and each is faked in tests.

**Ticket parsing.** Status, Blocked by, Runner and Auto-merge lines are parsed
whether bold (`**Status:**`) or plain. `Blocked by: None ...` means no blockers.
An unknown status word is a parse error that the morning report surfaces. It is
never treated as done.

**Jules API.** Sessions are created with `prompt`, `sourceContext` (repo source
plus `githubRepoContext.startingBranch` set to the default branch), `title`
(`<effort>-<NN>: <ticket title>`), `automationMode: AUTO_CREATE_PR`, and plan
approval not required. Auth is the `X-Goog-Api-Key` header. Quota is computed by
listing sessions and counting `createTime` within the last 24 hours. The limit
(100) and reserve (10) are config. The dispatcher records the session↔ticket link
by title prefix and claim branch, not in a state store.

**Prompt.** The dispatcher assembles: the engine's ticket skill (runner-agnostic,
with a "you are Jules" section that says don't push, don't open PRs, CI is
watched for you), the ticket path, and an instruction to read the target repo's
`AGENTS.md`, the ticket, its ADRs and `CONTEXT.md` first. Prompts are never
written to Actions logs.

**Tokens.** The dispatcher and gate use `PIPELINE_TOKEN` (a fine-grained personal
token scoped to the developer's repos) for claims, labels, merges and the report.
GitHub's built-in Actions token deliberately does not trigger further workflows,
so a merge made with it would not wake the dispatcher.

**Workflows.** The engine publishes reusable workflows: *dispatch* (triggered by
the caller on push to the default branch, hourly, and manual) and *integrity*
(triggered by the caller on pull requests, after the repo's own gates). It also
has a scheduled *morning-report* workflow in the engine repo itself, daily at
12:00 UTC (7 am Central). Callers pin a version tag.

**Claims.** A claim is `claim/<effort>/<NN>`, created with the Git refs API (a
conflict means already claimed). It is deleted on merge, or on release. It is
stale when no Jules session for it is live and no commit on its ticket branch is
newer than 12 hours.

**Labels.** `engine:hold`, `engine:escalated`, `engine:windows-waiting`. Paused
state is a repo Actions variable `TICKET_ENGINE_PAUSED` (set by the circuit
breaker, cleared by the developer).

**Fix attempts and escalation.** The dispatcher counts failed CI runs on a PR's
successive head commits. After the third failure it escalates on the worker's
behalf. It commits the ticket's `Status: blocked` and the escalation brief to the
PR branch, converts the PR to draft, and applies `engine:escalated`. The brief is
assembled from the ticket, the Jules session's activities, and the failing CI log
excerpt.

**Integrity checks.** These are exactly the seven in ADR 0001, plus the denylist
scan. Check 7 applies to test functions that are new in the PR. It runs them
against the base branch's source. Any new test that passes there produces a
`hold` with the test names, not a `fail`, because a refactor ticket can
legitimately add characterisation tests that already pass. Checks 4, 5 and
`Auto-merge: no` produce `hold`. The rest produce `fail`.

**Config.** Each target repo has a small engine config file: default branch,
daily cap, concurrency (default 2), Jules reserve, test command, gate commands,
and the paths that count as source and tests. The integrity gate and bootstrap
read the same file.

**Local worker.** A CLI command runs on the developer's machine against the local
clones listed in a local config. It finds `windows` frontier tickets, claims one,
works in a separate git worktree, and drives `agy -p ... --output-format json`.
It reads remaining quota via Antigravity's local status endpoint when available,
and otherwise falls back to detecting quota errors. It checkpoints, pauses on
quota, and resumes. The same command becomes the Phase 2 scheduled task.

**Bootstrap.** This is a skill in the engine, usable from Claude Code or Cowork,
backed by the bootstrap core. `adopt` migrates statuses, replaces the ticket skill
and tests-first script, adds the config, the caller workflows, the ticket
template, the rendered planner instructions and the `AGENTS.md` sections (the
implementation protocol, the no-people rule). It then applies the GitHub settings
and secrets, and prints the Jules setup script and the manual steps. It is
idempotent: existing developer-edited files are diffed and reported, never
overwritten silently.

**Rollout.** Phase 1 ships the engine, adopts Slackbot, and runs one real
Slackbot ticket set end to end. That run is a developer checkpoint. TDS-T8 and RBL
are adopted after it. A repo whose suite cannot run on Jules's Linux VM gets all
its tickets treated as `windows` until Phase 2.

## Testing Decisions

- **A good test here drives a core from the outside and asserts what the engine
  would do in the world**: which tickets it starts, what verdict it returns, which
  files and settings it produces. Never how it computed them. Tests are written
  from this spec's user stories before the code (the engine obeys its own
  tests-first rule and ADR 0001).
- **Three seams, all agreed with the developer:**
  1. **Dispatch**: world snapshot in, actions out. Scenario tests, for example: a
     fresh ticket set yields the first frontier tickets. A held blocker keeps its
     dependents off the frontier while an independent ticket starts. The reserve
     stops a start at 91 sessions. A third red CI yields an escalation. A second
     escalation within a day yields a pause. A 13-hour-old claim with no live
     session is released. A `windows` ticket is skipped and reported. Legacy or
     unknown statuses are surfaced, never treated as done.
  2. **Integrity**: fixture base/PR pairs in, a verdict and reasons out. One
     fixture per check proves the check fires, and one clean fixture proves it
     passes. Check 7 is tested with a real tiny fixture project whose new test
     does and does not fail on base.
  3. **Bootstrap**: run `adopt` on copies of small fixture repos shaped like
     Slackbot, RBL and TDS-T8 (bold statuses, legacy words, an old skill), and
     `new` on an empty directory. Assert the resulting files and the recorded
     GitHub operations. Run it twice and assert the second run changes nothing.
- **Adapters are faked** in core tests. Each adapter gets a small contract test
  against recorded API responses. No test calls the live Jules or GitHub API.
- **Every test must be seen to fail.** Break the rule it guards and confirm it
  goes red.
- **Prior art**: the target repos' `check_tests_first.py` (the tests-first gate
  being unified) and their ADR 0001 (the muting rules the integrity gate turns
  into checks).

## Out of Scope

- Phase 2 hardware: the ProDesk as a scheduled local worker, the EliteDesk as
  storage or report host, and self-hosted runners.
- Making any repo private, or self-hosted CI.
- Stacked PRs or building on held branches (ADR 0003).
- Any change to the target repos' application code, beyond the `adopt` migration
  and the people-denylist cleanup it reports.
- Planning automation. Grilling, specs and tickets stay with the developer and the
  planner in Claude.
- Using Claude for implementation or review inside the pipeline.
- Automatic reverts of merged PRs.

## Further Notes

- The local folder is named `ticker-engine` (sic). The GitHub repo should be named
  `ticket-engine` to match this spec. Rename the folder before the first push, or
  keep the folder name and set the remote to `ticket-engine`.
- Jules's environment setup script is configured in the Jules web UI per repo. The
  bootstrap can only print it.
- Jules's CI Fixer may also react to red CI. The dispatcher's three-attempt count
  is the authority either way. Whether CI Fixer runs count against the Jules
  allowance is unverified, so the first Slackbot run should check it.
- Slackbot CI uses Python 3.14 and Jules's VM ships 3.12. The setup script must
  install 3.14.
- RBL's rbl-ticket skill step 10 and AGENTS.md §11 step 10 (`gh pr checks
  --watch`) become runner-conditional when the engine's skill replaces them.
