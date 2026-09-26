# Spec: the box is the primary worker, Jules is overflow

**Status:** ready-for-agent

Governing decisions: ADR 0004, ADR 0006, ADR 0007. Glossary: `CONTEXT.md` (Box,
Overflow, Box status issue, Box silent, Box alert, Claim, Stale claim, Handoff,
Checkpoint, Escalation). Ticket numbers for this effort continue from 19.

## Problem Statement

Jules is the only worker for `Runner: any` tickets, and it is too slow for the
number of tickets the developer wants worked overnight. `Runner: windows` tickets
are worse off. Nothing runs them unless the developer starts `work-windows` by
hand.

The developer now has the box: a Windows mini PC that can run the Antigravity CLI
(`agy`) around the clock on his own quota. Phase 1's local worker cannot use it as
a real worker yet:

- It only takes `windows` tickets, runs one ticket per invocation, and then exits.
- It never pushes the ticket branch or opens a pull request. When `agy` succeeds,
  the orchestrator force-removes the worktree, so any commit `agy` did not push
  itself is lost.
- The worker instructions contradict each other. The local-worker section tells
  `agy` to push at every checkpoint and, a few lines later, never to push.
- The `agy` adapter was built against fakes. It reads a quota-status endpoint and a
  `quota_error` status that `agy` does not document. It does not pass
  `--dangerously-skip-permissions`, so in headless mode every tool call that needs
  approval is silently denied while the run still exits 0. It does not pass
  `--print-timeout`, so every run is cut off after the 5-minute default. Its resume
  path relies on `agy --continue`, which is also undocumented.
- Nothing can see whether the box is alive, working or capped, so the dispatcher
  cannot tell when Jules should step in.
- The dispatcher's stale-claim rule exists in the core but is never fed a
  last-progress time in live runs, so in practice no claim ever goes stale. A box
  claim stuck behind the weekly cap would hold its ticket, and everything
  depending on it, forever.
- Escalations reach the developer only as text in a ticket file. Nothing pushes a
  notification to his phone.

## Solution

The box runs one long-lived loop, `box-worker`, under the fenced `agent` Windows
account. It starts at boot and never needs the developer.

- **The box pulls work.** Each tick it resumes its own in-progress claims first.
  Otherwise it claims the lowest frontier ticket across its configured repos, any
  runner. It skips paused repos, respects each repo's daily cap, and runs one
  ticket at a time by default.
- **Claiming** creates the normal `claim/<effort>/<NN>` branch and writes
  `Claimed-by: box` into the ticket file on it.
- **Work** happens in a git worktree on the ticket branch. `agy` commits at each
  acceptance-criterion boundary and never pushes. The orchestrator pushes the
  branch on a timer while `agy` runs and again after every run. When the ticket
  reads `done`, the orchestrator opens the pull request through the GitHub API.
  When CI on that PR goes red, the box runs `agy` again with the names of the
  failing checks, up to the repo's fix-attempt limit. After that the dispatcher's
  existing escalation takes over.
- **Quota.** When `agy` reports a quota error, the box keeps its claim and branch
  and pauses. It retries at the reset time if the error gives one, otherwise
  hourly. If it keeps failing for more than 5 hours, it treats that as the weekly
  cap: it backs off to 12-hour retries and raises a box alert. A resume is always a
  fresh `agy -p` run on the pushed branch, with the last progress note in the
  prompt.
- **Visibility.** Every 30 minutes the box rewrites the pinned, locked *box status
  issue* in the engine repo: last check-in, current ticket, and paused state with
  its end time. Box alerts are issues opened from fixed templates. Nothing the box
  posts publicly contains free text.
- **The dispatcher stays a stateless GitHub Action.** It reads the box status issue
  each run.
  - While the box is healthy, it starts no Jules sessions and says so in the run
    report.
  - While the box is paused on quota, silent, or unreadable, it sends fresh
    `Runner: any` tickets to Jules as overflow.
  - It now feeds real last-progress times to the stale-claim rule. A box claim with
    no checkpoint for 8 hours is released, and a Jules claim keeps its 12-hour rule.
- **Handoff.** When Jules picks up a released box ticket, its prompt names the
  box's checkpoint branch and progress note, so it continues from there. If there
  is no checkpoint, it starts fresh. `windows` tickets are never handed to Jules.
- **A box that loses its claim** stops touching that ticket.
- **Escalations**, from either worker, also open an `escalation` issue in the
  target repo that @mentions the repo owner. The dispatcher closes it once the
  ticket leaves `blocked`.
- **A scheduled check** in the engine repo opens a "box silent" box alert when the
  box has not checked in for 12 hours.

## User Stories

1. As the developer, I want the box to take any ticket on the frontier, `windows` included, so that tickets are worked overnight without me starting anything.
2. As the developer, I want `Runner: windows` tickets to run on the box automatically, so that I no longer run them by hand.
3. As the developer, I want the box to start at boot and resume by itself after a reboot or power loss, so that an outage costs time, not work.
4. As the developer, I want the box to resume its own in-progress claims before claiming anything new, so that a ticket interrupted by a reboot is finished first.
5. As the developer, I want the box to claim tickets through the same `claim/<effort>/<NN>` branches as the dispatcher, so that the box and Jules can never both work one ticket.
6. As the developer, I want every claim to write `Claimed-by: box` or `Claimed-by: jules` into the ticket file, so that a finished ticket records who did it.
7. As the developer, I want the box to run one ticket at a time by default, with the number set in its local config, so that I can raise it later without a code change.
8. As the developer, I want the box to skip a repo whose dispatch is paused, so that the circuit breaker and my manual pause stop the box too.
9. As the developer, I want the box to respect each repo's daily cap, so that a runaway box has the same blast-radius limit as the dispatcher.
10. As the developer, I want the box to skip tickets that ticket lint rejects, so that it never burns quota on a ticket that cannot land.
11. As the developer, I want `agy` to work in a separate git worktree, never in the clone the box pulls from, so that a half-finished ticket never corrupts the next one.
12. As the developer, I want the box to push the ticket branch every 20 minutes while `agy` runs and again after every run, so that the pushed branch is always a recent checkpoint.
13. As the developer, I want the box never to remove a worktree that holds commits it has not pushed, so that work is never thrown away.
14. As the developer, I want the box, not `agy`, to hold the GitHub token and do every push, so that the agent process never handles the credential.
15. As the developer, I want the box to open the pull request itself when the ticket file reads `done`, so that box work reaches CI and the integrity gate the same way Jules work does.
16. As the developer, I want the PR body to come from a fixed template (ticket number, title, branch), so that nothing unexpected lands in a public PR.
17. As the developer, I want the box to react to red CI on its own PR by running `agy` again with the failing check names, so that routine test failures are fixed without me.
18. As the developer, I want the box's fix attempts to stop at the repo's `max_fix_attempts`, so that the dispatcher's existing three-strikes escalation stays the authority.
19. As the developer, I want `agy` run with `--dangerously-skip-permissions`, so that git, pytest and the gates are not silently denied in headless mode (ADR 0007).
20. As the developer, I want `agy` run with a `--print-timeout` value from local config, so that a real ticket is not cut off after five minutes.
21. As the developer, I want a run that hits the timeout to be resumed from its checkpoint like a quota pause, so that a long ticket is not lost to the timeout.
22. As the developer, I want a ticket that keeps timing out or failing without a quota error to escalate after a configured number of resumes, so that one bad ticket cannot loop forever.
23. As the developer, I want a quota error to keep the claim and the branch and pause the box, so that a capped box resumes where it stopped.
24. As the developer, I want the box to retry at the reset time when `agy`'s error gives one, and hourly otherwise, so that it resumes soon after the five-hour window refreshes.
25. As the developer, I want more than 5 hours of back-to-back quota failures treated as the weekly cap, with retries backed off to every 12 hours, so that a capped box does not spin.
26. As the developer, I want a weekly-cap box alert to be opened, @mentioning me, so that I know why nothing is moving.
27. As the developer, I want an expired Google login detected from `agy`'s auth error and raised as a box alert, so that I know to log in again under the `agent` account.
28. As the developer, I want each box alert closed by the box when its condition clears, so that open alerts always mean a current problem.
29. As the developer, I want at most one open alert of each kind, so that a long outage does not fill the repo with duplicates.
30. As the developer, I want a resumed run to be a fresh `agy -p` on the existing branch, with the last progress note in its prompt, so that resuming does not depend on an undocumented `--continue` flag.
31. As the developer, I want `agy` sessions that stop in `WAITING` answered with the engine's fixed auto-reply text up to `max_auto_replies` times, then escalated, so that the box follows ADR 0004 exactly as the dispatcher does for Jules.
32. As the developer, I want the box to rewrite a pinned, locked box status issue in the engine repo every 30 minutes, so that I can see at a glance on my phone what the box is doing.
33. As the developer, I want the box status issue to show last check-in time, current ticket (repo and number), and whether the box is paused on quota and until when, so that I can read it in two seconds.
34. As the developer, I want the box to create, pin and lock the box status issue itself if it does not exist, so that setup has one fewer manual step.
35. As the developer, I want silence judged by the check-in time the box writes into the body, not by GitHub's `updated_at`, so that an edit by anyone else does not look like a heartbeat.
36. As the developer, I want a scheduled check in the engine repo to open a "box silent" alert when the box has not checked in for 12 hours, so that a dead box reaches my phone.
37. As the developer, I want everything the box posts publicly to be fixed-template text of states, ticket IDs and times, never raw errors or logs, with a test that enforces it, so that nothing private leaks into a public repo (ADR 0002, ADR 0007).
38. As the developer, I want the box's full logs written to a gitignored folder on the box, so that I can debug over remote desktop without anything being committed.
39. As the developer, I want the dispatcher to start no Jules session while the box is healthy, so that Jules quota is kept for when the box cannot work.
40. As the developer, I want the dispatcher to send fresh `Runner: any` tickets to Jules while the box is paused on quota or silent, so that work continues when the box cannot take it.
41. As the developer, I want an unreadable or missing box status issue treated as "box silent", so that a broken heartbeat fails toward more work, not less.
42. As the developer, I want the run report to say why no Jules session started, for example "left for the box, which checked in at 03:12 UTC", so that "started 0" always carries its reason.
43. As the developer, I want the run report to show the box's state (healthy, paused until a time, silent, or unreadable) on every run, so that the routing decision is visible.
44. As the developer, I want `Runner: windows` tickets reported as waiting for the box, not for a Windows worker, so that the report matches the new setup.
45. As the developer, I want a box claim with no checkpoint commit for 8 hours released by the dispatcher, whether the box is capped or dead, so that one ticket cannot hold the queue hostage.
46. As the developer, I want the 8-hour threshold stored as a config value, so that I can tune it without a code change.
47. As the developer, I want Jules claims to keep the 12-hour stale rule, now measured from real commit times, so that the rule the glossary describes actually runs.
48. As the developer, I want a released `Runner: any` box ticket picked up by Jules from the box's checkpoint branch and progress note, so that hours of box work are not redone.
49. As the developer, I want a released ticket with no pushed checkpoint started fresh by Jules, so that handoff never depends on work that does not exist.
50. As the developer, I want released `Runner: windows` tickets never handed to Jules, so that they wait for the box.
51. As the developer, I want a box that finds its claim gone, or held by Jules, to stop pushing to that ticket and discard its local worktree, so that two workers never write to one ticket at once.
52. As the developer, I want every escalation, from the box or from Jules, to open an `escalation` issue in that ticket's repo @mentioning the repo owner and linking the branch or PR, so that GitHub Mobile notifies me instead of email.
53. As the developer, I want at most one open escalation issue per ticket, so that a repeated escalation does not open duplicates.
54. As the developer, I want the dispatcher to close an escalation issue once its ticket is `done` on the default branch or its claim branch is gone, so that open escalation issues are my actual to-do list.
55. As the developer, I want the escalation issue to contain only fixed-template text (ticket, branch or PR link, reason category), with the brief staying in the ticket file, so that nothing an agent wrote is copied into a new public place.
56. As the developer, I want the morning report to include the box's state, so that my daily summary says whether the box worked overnight.
57. As the developer, I want a written box setup runbook in the engine repo, so that I can rebuild the box from scratch.
58. As the developer, I want a practice run on a throwaway ticket before relying on the box, so that the first real overnight run is not also the first test.
59. As the planner, I want the box's scheduling decisions in a pure core, so that every routing, pause and backoff rule is testable from a snapshot with a fixed clock.
60. As the planner, I want the dispatcher's overflow and stale-claim decisions to stay in `DispatchCore.evaluate`, so that there is still one place that decides what starts.
61. As the planner, I want the box to decide what is on the frontier through `DispatchCore.compute_frontier`, so that there is still one definition of blocked.
62. As a worker implementing this, I want the local-worker section of the ticket skill to give one consistent rule (commit at each criterion, never push, never open a PR), so that `agy` is not told two contradictory things.
63. As a worker implementing this, I want the `agy` adapter's outcome classes backed by recorded output fixtures, so that I can see exactly which output maps to which outcome.
64. As a reader of the public engine repo, I want the box status issue locked, so that nobody else can post into the box's heartbeat.

## Implementation Decisions

### Seams (confirmed with the developer)

Five test seams, highest first:

1. **`DispatchCore.evaluate`** (exists): overflow gating, the box-claim stale rule,
   the handoff start.
2. **`BoxCore.next_step`** (new, pure): the box's decision each tick.
3. **Box status render/parse** (new, pure): one module that owns the box status
   issue body and the alert templates.
4. **`LocalWorker`** (exists, extended): the orchestrator, tested with fake `agy`,
   git and GitHub.
5. **`AgyDriver`** (exists, reworked): classifies `agy` output, tested with
   recorded fixtures.

### The box loop

- A new console script, `box-worker`, runs forever: tick, act, sleep. Each tick it
  builds a `BoxWorld` snapshot, asks `BoxCore.next_step`, and carries the step out.
  `box-worker --once` runs one tick, then exits. The old `work-windows` script
  keeps its current behaviour.
- The box is not the dispatcher, so it may keep local state. It keeps two things in
  its logs folder:
  - a start ledger, which counts starts per repo against the daily cap;
  - its quota-pause record: the first quota failure time, and the next retry time.
  Losing either file only resets it. Everything else it re-reads from GitHub each
  tick.
- **`BoxWorld`** (plain data) contains:
  - per configured repo: the parsed tickets (read from a fresh pull of the default
    branch), the repo's `RepoConfig`, whether it is paused, the claim branches with
    their `Claimed-by` value, and the box's own open PRs with CI conclusion and
    fix-attempt count;
  - starts in the last 24 hours, from the ledger;
  - the quota-pause record;
  - the auth-failure flag;
  - the time of the last status-issue write;
  - `now`.
- **`BoxCore.next_step(world) -> BoxStep`**. The step types are `WriteStatus`,
  `ResumeClaim(repo, ticket)`, `FixCI(repo, ticket, pr, failing_checks)`,
  `ClaimTicket(repo, ticket)`, `RaiseAlert(kind)`, `CloseAlert(kind)`, and
  `Wait(until)`. It picks the first rule that applies:
  1. If more than `status_interval_minutes` have passed since the last status
     write, `WriteStatus`.
  2. If quota-paused and `now` is before the retry time, `Wait(retry time)`.
  3. If a box-claimed ticket has an open box PR with red CI and attempts remain,
     `FixCI`.
  4. If a box-claimed ticket is unfinished, `ResumeClaim`.
  5. If a slot is free: the lowest frontier ticket, first configured repo first,
     that is unclaimed, lint-clean, in an unpaused repo under its daily cap,
     gets `ClaimTicket`.
  6. Otherwise, `Wait(now + poll_interval_minutes)`.
- **The frontier comes from `DispatchCore.compute_frontier`**, the same as today.
  The box does not reimplement it. The box takes every runner value.
- **Quota protocol** (resolves the open item from grilling):
  - On a quota error, record the first failure time if none is recorded yet. The
    retry time is the error's reset time plus 60 seconds if `agy` gave one,
    otherwise `now + 1 hour`.
  - When the first failure is more than `weekly_cap_after_hours` (default 5) old,
    the retry becomes `now + weekly_cap_backoff_hours` (default 12), and the core
    returns `RaiseAlert(weekly_cap)` once.
  - Any successful `agy` run clears the record and returns `CloseAlert(weekly_cap)`.
- **Auth failure**: `RaiseAlert(login_expired)` once, then hourly retries. A
  success closes the alert.
- **The box never releases its own claim.** Releasing is the dispatcher's job, so
  that only one place decides it.

### Local worker orchestration

- **Claiming** creates the claim branch at the default-branch SHA. It then commits
  the ticket file on the claim branch with a `Claimed-by: box` line added directly
  under `Status:`. Status words are unchanged.
- **The ticket branch** is `ticket/<effort>-<NN>-<slug>`. It is created from the
  claim branch's head, so the `Claimed-by` line travels into the PR. If a ticket
  branch already exists on the remote, the box resumes from it and never recreates
  it.
- **Before every resume, push and PR**, the orchestrator re-reads the claim branch.
  If the branch is gone, or reads `Claimed-by: jules`, the orchestrator abandons the
  ticket. It deletes the local worktree, leaves the remote ticket branch alone, and
  logs the loss locally. This check is the only exception to "never remove a
  worktree with unpushed commits". A ticket that belongs to another worker is no
  longer the box's to save.
- **Pushing**: while `agy` runs, a timer pushes the ticket branch every
  `checkpoint_push_minutes` (default 20). The branch is pushed again after every
  run, whatever the outcome.
  - The push authenticates with the PAT through per-process git configuration
    environment variables (an `http.extraHeader`). The token is never written to
    git config on disk and never logged.
  - A push failure is logged locally and retried on the next push. It never
    discards work.
- **Worktrees** are removed only after the ticket's PR has merged, the ticket was
  abandoned (see above), or the branch has no unpushed commits and the ticket is
  `done`.
- **The PR** is opened through the GitHub REST API when the worktree's ticket file
  reads `Status: done`. Base is the repo's default branch, head is the ticket
  branch. The title is `<effort>-<NN>: <ticket title>` (the same shape as Jules
  session titles, which the dispatcher already matches on). The body is a fixed
  template.
- **FixCI** runs `agy -p` on the ticket branch. The prompt is the normal prompt plus
  a fixed section listing the names of the failing checks. Check names and
  conclusions are read from the public repo's check-runs API.
- **Escalation by the box** covers a ticket that has used up
  `max_resumes_per_ticket` (default 3) without finishing, and repeated `WAITING`
  past `max_auto_replies`. The orchestrator:
  1. applies `apply_escalation_to_ticket_text` to the ticket file, with a brief
     from `assemble_escalation_brief`;
  2. commits and pushes it;
  3. opens the PR as a draft, labelled `engine:escalated`;
  4. opens the escalation issue (see Escalation issues).
- **Logs**: all box logging goes to rotating files in a `logs/` folder of the box's
  engine checkout. That folder is added to `.gitignore`.

### `agy` adapter

- **Start and resume are both**
  `agy -p <prompt> --output-format json --dangerously-skip-permissions --print-timeout <value>`,
  run in the worktree. `<value>` comes from local config and is passed verbatim.
  `continue_session`, `read_quota` and `QuotaInfo` are removed, along with the
  `agy_quota_url` and `agy_quota_reserve_pct` config keys. There is no documented
  quota source. The CONTEXT entry *Quota reserve* is corrected in the held ticket
  below.
- **`AgyResult` gains `outcome`**, one of `success`, `quota`, `auth`, `timeout`,
  `waiting`, `failed`. The mapping:
  - `SUCCESS` with exit code 0 is `success`.
  - `WAITING` is `waiting`.
  - `CANCELED` and `INTERRUPTED` are `timeout`.
  - `ERROR` whose message matches one of the local config's `quota_error_patterns`
    is `quota`.
  - `ERROR` matching `auth_error_patterns` is `auth`.
  - Anything else, and unparseable output, is `failed`.
  - A reset time is taken from the JSON if a field for one is present. Otherwise it
    is `None`.
  The patterns live in config because the exact wording is undocumented and must
  be confirmed in the practice run.
- **`waiting`** is answered by a fresh run whose prompt adds `AUTO_REPLY_TEXT`. The
  box counts replies per ticket, in memory, and escalates past `max_auto_replies`.

### Box status issue and alerts

- **One pure module owns every public box text**:
  - `render_box_status(BoxStatus) -> str` and `parse_box_status(str) -> BoxStatus | None`;
  - `render_box_alert(kind, facts) -> (title, body)`;
  - `render_escalation_issue(facts) -> (title, body)`.
- **The inputs are typed, with no free text.** `BoxStatus` holds:
  - `checked_in_at` (UTC datetime);
  - `current` (repo `owner/name` plus ticket number, or none);
  - `paused_until` (datetime or none);
  - `state`, one of `working`, `idle`, `paused_quota`, `paused_weekly_cap`,
    `login_expired`.
  Repo names are checked against `^[\w.-]+/[\w.-]+$`.
- **The body is fixed lines** with machine-readable `key: value` pairs, for example
  `Checked in: 2026-09-26T03:12Z`, `State: paused_quota`,
  `Paused until: 2026-09-26T08:00Z`, `Current: owner/repo #21`. A body the parser
  cannot read returns `None`.
- The status issue is found by the label `engine:box-status`. If it is missing, the
  box creates it, pins it (GraphQL `pinIssue`) and locks it.
- **Box alerts** are issues labelled `engine:box-alert`, one open per kind (the kind
  is in the title), @mentioning the engine repo's owner. The kinds are
  `weekly_cap`, `login_expired`, and `box_silent`.
- **The @mention handle is always the repo owner**, taken from the repository name
  at runtime, never written as a literal (roles, not names).

### Dispatcher changes

- **`WorldSnapshot` gains `box: BoxStatus | None`** and
  `box_status_error: str` (the text of a failed read, for the report). The live
  dispatcher reads the engine repo's status issue (public) and parses it.
- **Overflow gate in `evaluate`**: the box counts as available when `box` is not
  `None`, its state is `working` or `idle`, and
  `now - checked_in_at < box_silent_hours` (default 12). While the box is available,
  no `StartTicketAction` is returned for any ticket. When it is not, the existing
  rules start `Runner: any` tickets as today. `Runner: windows` tickets are never
  started either way.
  - `DispatchResult` gains `left_for_box: list[Ticket]` and
    `box_state: str`, one of `available`, `paused`, `silent`, `unreadable`.
  - The run report renders both. The "skipped windows" wording becomes "waiting for
    the box".
- **`Claim` gains `claimed_by: str`** (`box`, `jules`, or empty for pre-Phase-2
  claims, which are treated as `jules`) and keeps `last_commit_time`, whose meaning widens to
  "last progress time". The live dispatcher fills both for every
  claim:
  - `claimed_by` from the claim branch's ticket file;
  - the progress time from the newest commit on the ticket branch
    `ticket/<effort>-<NN>-<slug>` if it exists, otherwise the claim branch head.
  A read failure leaves the time as `None`, and the claim is then never released.
- **The stale rule in `evaluate`**:
  - Box claims are released at `box_stale_claim_hours` (new `RepoConfig` key,
    default 8), live session or not.
  - Jules claims keep `stale_claim_hours` (12) and the live-session exception.
  - The live dispatch path now calls this sweep on every run. Today it is never
    reached.
- **Handoff**: when a started ticket has a box ticket branch ahead of the default
  branch, `StartTicketAction` carries `handoff_branch` and `handoff_note`. The note
  comes from `_extract_progress_note` on that branch's ticket file.
  `assemble_prompt` gains an optional handoff section: fetch that branch, merge it,
  continue from the note's "Next:". The Jules session still starts from the default
  branch, so the PR base stays correct. If the fetch fails inside Jules, the prompt
  says to start the ticket fresh.
- **Claimed-by for Jules**: the live dispatcher commits `Claimed-by: jules` to the
  claim branch after creating it. `_final_step_rule` also tells every worker to
  keep the `Claimed-by:` line, so the PR carries it.
- **The parser** reads `Claimed-by:` into `Ticket.claimed_by`. An unknown value is a
  `ParseFinding`, never a crash. Ticket lint does not look at it.

### Escalation issues

- These are opened in the ticket's target repo, labelled `escalation`, with the
  title `Escalation: <effort>-<NN> <title>`. The body comes from
  `render_escalation_issue`: the ticket number, a link to the branch or PR, and a
  reason category (`ci_failed`, `kept_asking`, `resumes_exhausted`), plus an
  @mention of the repo owner.
- The live dispatcher opens one for `EscalatePRAction` and
  `EscalateWaitingSessionAction`, and the box opens one for its own escalations.
  Before creating, both search for an open issue with the same title.
- Each run, the dispatcher closes open `escalation` issues whose ticket is `done`
  on the default branch or whose claim branch no longer exists.

### GitHub adapter additions

`GitHubClient` gains:

- `create_issue`, `update_issue_body`, `close_issue`, `find_open_issue(label, title)`;
- `lock_issue`, `pin_issue` (GraphQL);
- `create_pull_request(draft=)`;
- `get_branch_head_time`;
- `list_check_runs(ref)`.

Each is injectable and faked in tests, like the existing methods.

### Held changes (always `Auto-merge: no`, leaves)

- `.github/workflows/box-silent-check.yml` in the engine repo, every 3 hours:
  1. parse the status issue;
  2. if the box is silent for 12 hours or more and no `box_silent` alert is open,
     open one;
  3. if it is not silent and one is open, close it.
  The check is a script over the same pure module.
- The `dispatch.yml` step summary gains nothing new. The report does it.
- `CONTEXT.md`:
  - *Quota reserve*: the local worker has no pre-flight reserve; it pauses on a
    quota error.
  - *Local worker*: it takes any ticket.
  - *Checkpoint*: the orchestrator pushes.
- `docs/agents/issue-tracker.md`: document the `Claimed-by:` line.
- ADR 0007 amendment: the box's token also needs **Variables: read** on target
  repos, to read `TICKET_ENGINE_PAUSED`. See Further Notes.

### Local config (`~/.ticket-engine-local.toml` under the `agent` account)

New keys, with defaults:

| Key | Default |
|---|---|
| `concurrency` | 1 |
| `poll_interval_minutes` | 10 |
| `status_interval_minutes` | 30 |
| `checkpoint_push_minutes` | 20 |
| `print_timeout` | `"7200"` |
| `max_resumes_per_ticket` | 3 |
| `weekly_cap_after_hours` | 5 |
| `weekly_cap_backoff_hours` | 12 |
| `quota_error_patterns` | `["quota", "rate limit", "exhausted"]` |
| `auth_error_patterns` | `["auth", "login", "credential"]` |
| `engine_repo` | `"ilegault/ticket-engine"` |
| `logs_dir` | — |

`repos` is unchanged.

## Testing Decisions

- **A good test drives a core from the outside and asserts what the engine would
  do in the world**: which step the box takes, which ticket the dispatcher starts or
  leaves, which claim is released, and the exact text of an issue body. Never the
  call order inside an orchestrator, beyond what the world sees.
- **The cores and the parser are real in every test.** Build tickets through the
  real parser with the `ticket(...)` helper pattern from `tests/test_run_report.py`.
  Fake GitHub, Jules, `agy`, git, sleep and the clock. No test spawns a real `agy`.
- **`DispatchCore.evaluate`**: extend `tests/test_dispatch_scenarios.py`.
  - Box available leaves the frontier untouched and fills `left_for_box`. Paused,
    silent and unreadable each start Jules.
  - `windows` tickets are never started in any state.
  - A box claim at 7h59m stays and at 8h is released. A Jules claim with a live
    session at 13 hours stays.
  - A claim with no progress time is never released.
  - A handoff start carries its branch and note.
  - Rewrite the existing stale-claim tests in place under their current names. No
    test deletions are expected.
- **`BoxCore.next_step`**: a new `tests/test_box_core.py`, from snapshots with a
  fixed `now`. One test per rule above, plus:
  - order of precedence: resume beats claim, and FixCI beats resume;
  - the quota timeline: first failure, then an hourly retry, then the 5-hour mark
    raising the alert once, then 12-hour backoff, then a success closing it;
  - the daily cap, paused repos, lint-held tickets, and the concurrency slot.
- **Box status render/parse**: a new `tests/test_box_status.py`.
  - A render then parse round trip for every state.
  - An unparseable body returns `None`.
  - **The public-text test**: every line of every rendered status, alert and
    escalation body matches a full-line allowlist regex, for every state and kind.
    A repo name failing the pattern is rejected.
  - The test must be seen to fail when a renderer is given a free-text field.
- **`LocalWorker`**: extend `tests/test_local_worker.py` with a fake git runner
  and a fake GitHub client that record effects. Cover:
  - `Claimed-by: box` committed to the claim branch;
  - the ticket branch created from the claim head;
  - a push after every run, including a quota failure;
  - no worktree removal while commits are unpushed;
  - PR opened only when the ticket file reads `done`;
  - claim gone or `jules` means the worktree is abandoned and nothing is pushed;
  - escalation writes the brief, a draft PR and one issue;
  - the token never appears in any logged string.
- **`AgyDriver`**: recorded outputs in a new `tests/fixtures/agy/`, one per
  outcome. Assert the classification and the exact argument list (both flags
  present, prompt passed as one argument).
- **Prior art**: `tests/test_waiting_sessions.py` for marker-counted replies,
  `tests/test_live_dispatch.py` for orchestrator effects on fakes, and
  `tests/test_run_report.py` for asserting rendered report text whole-row.

## Out of Scope

- Remote access, networking and file sharing for the box, and the vault storage.
  These are separate projects (ADR 0007 rule 6).
- More than one box, and box concurrency above 1 in practice. It is configurable
  but not exercised.
- Feeding escalations into the circuit breaker (ADR 0004 consequence). Unchanged.
- Moving the dispatcher onto the box. Rejected in ADR 0006.
- Windows Update policy, BIOS settings, the `agent` account, the Google login and
  PAT creation. These are the developer's setup steps, covered by the runbook
  ticket.
- Any change to the integrity gate.
- Moving `v1` and confirming on Slackbot. That is a developer ticket at the end.

## Further Notes

- **Unverified against real `agy`**, and to be confirmed by the developer's practice-run ticket before any real overnight run:
  - the quota and auth error wording (hence the config patterns);
  - whether any reset time appears in the JSON;
  - the unit `--print-timeout` expects;
  - that the status for a timed-out run is `CANCELED` or `INTERRUPTED`;
  - that a prompt of the assembled length fits the Windows command-line limit
    (32,767 characters).
  If it does not, the adapter must pass the prompt another way. That becomes a
  follow-up ticket, not a guess now.
- **Unverified against Jules**: whether a Jules VM can `git fetch` a branch other
  than its starting branch. The handoff prompt fails safe (start fresh) if it
  cannot.
- **A flagged deviation from ADR 0007 rule 3**: reading `TICKET_ENGINE_PAUSED`
  needs Variables: read on the target repos, which the agreed token lacks.
  Everything else the box reads is public. The spec assumes the developer adds that
  one read permission, recorded by amending ADR 0007 in a held ticket. If he would
  rather not, the box cannot honour a repo pause, and that choice is his to make
  before ticket-writing.
- **Suggested ticket order** (for `/to-tickets`):
  1. the `agy` adapter;
  2. the box status module;
  3. `BoxCore`;
  4. `LocalWorker` claim, push and PR;
  5. `LocalWorker` FixCI and escalation;
  6. the `box-worker` loop and status writes;
  7. the parser's `Claimed-by`, Jules `Claimed-by` and the handoff prompt;
  8. the dispatcher's claim progress times and stale rule;
  9. the dispatcher's overflow gate and run report;
  10. escalation issues;
  11. held leaves: the silent-check workflow, CONTEXT, issue-tracker and ADR 0007
      amendment;
  12. developer leaves: the box setup runbook and practice run, then moving `v1`
      and confirming.
- The spec deliberately keeps "the dispatcher keeps no state of its own"
  (AGENTS.md invariant 2). The box's ledger and pause record are the box's, not the
  dispatcher's.
