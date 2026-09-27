# Spec: Claude Sonnet as the box's quota fallback for agy

**Status:** ready-for-agent

Governing decisions: ADR 0006, ADR 0007. Glossary: `CONTEXT.md` (Box, Worker,
Local worker, Overflow, Quota reserve, Checkpoint). Ticket numbers for this
effort continue from 39.

## Problem Statement

The box's local worker has exactly one implementer: `agy`. When `agy` reports a
quota error, the box has no choice but to pause — `work-windows` sleeps out the
reset and resumes with `agy` again, and `box-worker`'s loop returns the quota
result straight to `BoxLoop`, which pauses the entire box until the retry time
(`BoxCore.after_quota_error`). While the box is paused, the dispatcher's overflow
gate (ADR 0006 rule 3) sees the box status issue showing a paused state and sends
the ticket's `Runner: any` counterpart to Jules instead.

The developer has a paid Claude subscription that sits completely idle while this
happens, and Jules — by far his least-favoured worker, kept only because
something has to cover for a paused box — ends up doing routine work that a
five-hour agy quota window, not a real capacity problem, caused. There is
currently no way for the box to reach for a second implementer on the same ticket
before giving up to a pause.

## Solution

A new `sonnet.py` adapter, built to the exact same shape as `agy.py`, drives
Claude Code headlessly (`claude -p`) under the box's own Claude subscription
login — no API key, no `--bare`. `LocalWorker` gains one new private method,
`_start_with_fallback`, which replaces every place it currently calls
`self.agy_driver.start(...)` directly: it tries `agy` first, and only when
`agy`'s result is a quota error does it try the optional `sonnet_driver` on the
exact same prompt and worktree, returning whichever result comes back.

Nothing downstream of that one method changes. `_resolve_outcome`'s
resume/auto-reply/escalation counters, `box_mode`'s return-to-caller behaviour,
`BoxCore`'s quota timeline, the box status issue, and the dispatcher's overflow
gate to Jules are untouched — because a driver's result still has the same shape
(`success`, `outcome`, `quota_error`, `reset_at`) whichever tool produced it. The
box only actually pauses, and Jules only actually receives overflow work, once
*both* agy and Sonnet report a quota error for the same call. Jules's demotion to
true last resort falls out of this automatically; it needs no change to ADR
0006's overflow rule, `dispatch.py`, or `box_status.py`.

## User Stories

1. As the developer, I want the box to try Claude Sonnet on a ticket the moment `agy` reports a quota error, so that the box keeps working through agy's five-hour and weekly quota windows instead of pausing.
2. As the developer, I want Sonnet invoked as a plain `claude -p` session under my own Claude subscription login, so that the box draws on the plan I already pay for rather than a separately metered API key.
3. As the developer, I want the box's `agent` Windows account to log into my Claude account once, interactively, the same way it already logs into my Google account for `agy`, so that headless `claude -p` runs need no further setup.
4. As the developer, I want the Sonnet fallback entirely optional and off by default, so that a box with no `claude` login configured behaves exactly as it does today.
5. As the developer, I want every one of `agy`'s four call sites — the first attempt, a checkpoint resume, an auto-reply to a waiting session, and a CI fix attempt — to try Sonnet the same way, so that a ticket running low on agy quota partway through doesn't stall on whichever call happens to hit the cap.
6. As the developer, I want the same ticket branch, worktree, and prompt used for the Sonnet attempt as for the `agy` attempt it replaces, so that a ticket's implementation stays one continuous piece of work regardless of which tool did which part.
7. As the developer, I want the fallback to trigger only on a quota outcome, never on `waiting`, `timeout`, or a plain `failed`, so that Sonnet is reached for specifically when agy's account is out of room, not as a second opinion on a ticket agy is still able to attempt.
8. As the developer, I want the box to fall back to its existing pause behaviour, and therefore Jules overflow, only once Sonnet's own attempt also reports a quota outcome, so that Jules stays the true last resort it's meant to be.
9. As the developer, I want each `claude -p` call given a wall-clock timeout from local config, playing the same role agy's `--print-timeout` does, so that a hung Sonnet session doesn't block the box indefinitely.
10. As the developer, I want a Sonnet run's outcome classified from the CLI's own documented signals — the final `result` message and any `system/api_retry` event's `error` category — rather than a guessed error-message pattern, so that classification doesn't depend on scraping free text the way agy's undocumented errors currently do.
11. As the developer, I want the retry categories `rate_limit`, `overloaded`, `billing_error`, and `account_on_hold` all classified as `quota`, so that any of the ways my plan's usage can run dry are treated the same way.
12. As the developer, I want the retry categories `authentication_failed`, `oauth_org_not_allowed`, and `cloud_credential_error` classified as `auth`, so that an expired login on the box surfaces the same way an expired `agy` login already does.
13. As the developer, I want the Sonnet call made with `--permission-mode bypassPermissions` and `--permission-prompts none`, so that it runs fully unattended the same way `agy` runs with `--dangerously-skip-permissions` (ADR 0007).
14. As the developer, I want the Sonnet call made without `--bare`, so that it keeps my subscription login rather than requiring a metered `ANTHROPIC_API_KEY`.
15. As the developer, I want a Sonnet call to never end in a `waiting` outcome, so that the fallback needs no new auto-reply handling beyond what already exists for `agy`.
16. As the developer, I want the Sonnet driver's `run_fn` injectable the same way `AgyDriver`'s is, so that tests never spawn a real `claude` process.
17. As the developer, I want each ticket's PR, checkpoint pushes, and escalation brief to look exactly the same whether `agy` or Sonnet did the work, so that nothing about a landed ticket depends on which implementer touched it.
18. As the developer, I want the box's own rotating log (never anything public) to record which driver actually produced each attempt's result, so that I can tell from the log file whether a ticket used the fallback, without anything about it appearing in a public PR, issue, or the box status issue.
19. As the developer, I want a Sonnet call's `total_cost_usd` (from `--output-format json`'s cost field) written to the box's local log, so that I have a record of what the fallback is costing even though it isn't billed per token.
20. As the developer, I want the fallback's enablement and timeout to live in local config (`~/.ticket-engine-local.toml`), never hardcoded, so that I can tune or disable it without a code change.
21. As a worker implementing this, I want `sonnet.py` built to the same shape as `agy.py` — a frozen result dataclass with `outcome`, `success`, `quota_error`, `reset_at`, `session_id`, `raw` — so that `LocalWorker` can treat either driver identically.
22. As a worker implementing this, I want the classifier tested against recorded `stream-json` fixtures, one per outcome, the same way `AgyDriver` is tested against recorded fixtures, so that classification is proven against real shapes, not assumptions.
23. As a planner, I want zero changes to `BoxCore`, `BoxLoop`, `dispatch.py`, or `box_status.py`, so that the fallback's correctness rests entirely on `LocalWorker`'s existing, already-tested retry and escalation loop.
24. As the developer, I want this to ship without touching `.github/`, `docs/adr/`, `AGENTS.md`, or `CONTEXT.md` in the same PR as the code, so that the working fallback isn't held behind a docs review it doesn't need — a documentation-only ticket can follow separately, held and a leaf.

## Implementation Decisions

### The seam

One seam: `LocalWorker._start_with_fallback(prompt, cwd) -> object` (new private
method), replacing the four places `LocalWorker` currently calls
`self.agy_driver.start(...)` directly — `run_one`'s first attempt,
`_resume_from_checkpoint`, `_send_auto_reply`, and `fix_ci`. No other module
changes. Confirmed with the developer.

### `sonnet.py` adapter

New module `src/ticket_engine/sonnet.py`, mirroring `agy.py`'s shape:

- `SonnetResult`, a frozen dataclass: `outcome: str`, `success: bool = False`,
  `quota_error: bool = False`, `reset_at: datetime.datetime | None = None`,
  `session_id: str | None = None`, `raw: dict = field(default_factory=dict)`,
  plus `cost_usd: float | None = None` (new; not on `AgyResult`, so
  `_start_with_fallback` only reads the fields the two share).
- `RunFn` alias and a `_default_run` that calls `subprocess.run(..., timeout=...)`
  and classifies a caught `subprocess.TimeoutExpired` as `outcome="timeout"`.
- `SonnetDriver.__init__(run_fn=None, timeout_seconds=7200)`.
- `SonnetDriver.start(prompt, cwd) -> SonnetResult`: invokes
  `claude -p <prompt> --output-format stream-json --permission-mode bypassPermissions --permission-prompts none`
  (no `--bare`) and classifies the parsed stream:
  - a final `result`-type message with no error → `success`;
  - a `system/api_retry` event whose `error` is one of `rate_limit`,
    `overloaded`, `billing_error`, `account_on_hold` → `quota` (no reset time is
    given by this event; `reset_at` stays `None`, so `_seconds_until_reset`'s
    existing default buffer applies exactly as it already does when `agy` gives
    no reset time);
  - a `system/api_retry` event whose `error` is `authentication_failed`,
    `oauth_org_not_allowed`, or `cloud_credential_error` → `auth`;
  - a caught timeout → `timeout`;
  - anything else — non-zero exit, unparseable stream, or any other retry
    category (`invalid_request`, `model_not_found`, `server_error`,
    `max_output_tokens`, `unknown`) → `failed`;
  - `waiting` is never produced: `--permission-prompts none` guarantees the run
    always concludes rather than stopping to ask.
  `cost_usd` is read from the final `result` message's `total_cost_usd` when
  present.

### `LocalWorker` changes

- New constructor parameter `sonnet_driver: SonnetDriver | None = None`,
  defaulting to `None`, injected exactly like `agy_driver` and `git_runner`
  today.
- New private method:

  ```
  def _start_with_fallback(self, prompt: str, cwd: str) -> object:
      result = self.agy_driver.start(prompt, cwd=cwd)
      if result.quota_error and self.sonnet_driver is not None:
          logger.info("agy out of quota; falling back to Sonnet for this attempt.")
          return self.sonnet_driver.start(prompt, cwd=cwd)
      return result
  ```

  The log line is illustrative, not prescriptive — the ticket may word it
  differently — but it goes through `logging.getLogger(__name__)` (never
  `print()`, AGENTS.md §5) and stays in the box's local rotating log only; it
  never reaches a public surface (ADR 0007 rule 4/5 already governs everything
  this module logs).
- The four call sites listed above are changed to call `self._start_with_fallback(...)`
  in place of `self.agy_driver.start(...)`.
- `_resolve_outcome`, `BoxLoop`, `BoxCore`, `dispatch.py`, and `box_status.py` are
  **not modified**. Their quota handling — sleep-and-resume in `work-windows`
  mode, return-to-caller and the box-wide pause in `box_mode` — now triggers only
  once `_start_with_fallback` itself returns a quota result, i.e. once agy and
  (when configured) Sonnet have both reported quota for that call.

### Config

`LocalWorkerConfig` (`local_config.py`) gains two keys, both defaulting to
today's behaviour when unset:

| Key | Default |
|---|---|
| `sonnet_enabled` | `false` |
| `sonnet_timeout_seconds` | `7200` |

When `sonnet_enabled` is `false`, `build_loop` and `work-windows`'s wiring
construct `LocalWorker` with `sonnet_driver=None`, and `_start_with_fallback`
behaves exactly as today's direct `agy_driver.start` call. `RepoConfig`
(`config.py`, the per-target-repo file) is untouched — this is a box-local
capability, not a per-repo tunable.

### Auth

The box's `agent` Windows account runs `claude` interactively once to log in,
exactly like its one-time `agy` login under ADR 0007. No API key, no `--bare`.

## Testing Decisions

- A good test still drives `LocalWorker` from the outside with fakes and asserts
  what the engine would do — which driver's result decided the ticket's fate,
  whether a PR was opened, what got pushed — never the internal call order
  beyond what's observable through the fakes.
- **`sonnet.py`**: recorded `stream-json` fixtures in a new `tests/fixtures/sonnet/`,
  one per outcome (`success`; `quota` from each of the four retry-error
  categories; `auth`; `timeout` via a fake `run_fn` raising `TimeoutExpired`;
  `failed`) — the same pattern `tests/fixtures/agy/` already uses for
  `AgyDriver`. Assert the classification and the exact argument list
  (`--output-format stream-json`, `--permission-mode bypassPermissions`,
  `--permission-prompts none`, no `--bare`).
- **`LocalWorker._start_with_fallback`**: extend `tests/test_local_worker.py` with
  a fake `agy_driver` and a fake `sonnet_driver`, both recording call counts and
  arguments.
  - agy quota, no sonnet configured → returns agy's quota result unchanged
    (proves no regression in today's behaviour).
  - agy quota, sonnet configured and succeeds → sonnet's result is what the
    caller sees; the PR/push/checkpoint machinery runs off sonnet's result
    exactly as it would off agy's.
  - agy quota, sonnet also quota → the combined result is a quota outcome, so
    `box_mode`'s existing return-to-caller and `work-windows`'s existing
    sleep-and-resume behave exactly as today (assert against the *existing*
    tests for those paths, not new ones — nothing about them should need to
    change).
  - agy success → sonnet is never called (assert on the fake's call count).
  - agy `failed`/`timeout`/`waiting` → sonnet is never called; existing
    resume/auto-reply/escalation counters behave exactly as today.
  - each of the four call sites (initial run, resume, auto-reply, fix_ci) goes
    through `_start_with_fallback`, not `agy_driver.start` directly — one
    assertion per site.
- No existing test in `tests/test_local_worker.py`, `tests/test_box_core.py`,
  `tests/test_box_worker.py`, or the dispatch/box-status suites should need to
  change, since `BoxCore`, `BoxLoop`, and `dispatch.py` are untouched by this
  effort — the ticket should say so and the full existing suite must still pass
  unmodified.
- Prior art: `tests/fixtures/agy/` and its outcome-classification tests are the
  direct model for `tests/fixtures/sonnet/`; `tests/test_local_worker.py`'s
  existing fake-driver pattern is the direct model for adding a second fake.

## Out of Scope

- Any change to `BoxCore`, `BoxLoop`, `dispatch.py`, `box_status.py`, the box
  status issue's rendered states, or ADR 0006's Jules-overflow rule. Jules's
  demotion to true last resort is a consequence of this fallback, not a code
  change.
- Parallelism, or Sonnet running its own sub-agents across several tickets at
  once. Sonnet is invoked as a single per-ticket implementer, the same shape as
  `agy`, one call at a time — not a supervisor that fans out sub-agents across
  tickets. The standing one-ticket-one-PR model is unchanged (confirmed with the
  developer during grilling).
- `--max-turns`/`--max-budget-usd` as an additional per-call cap. Worth adding
  later; not required here, since the wall-clock timeout and the existing
  `max_resumes_per_ticket` ceiling already bound a runaway ticket.
- Recording which driver worked a ticket anywhere public — the box status issue,
  a PR body, or the ticket file. Confirmed with the developer: this stays
  box-local-log-only.
- The one-time `claude` login setup step itself, and any change to the box setup
  runbook beyond noting that one step. That's the developer's bench work.
- Any change to `RepoConfig`/`.ticket-engine.toml` (the per-target-repo file).
  This capability is entirely box-local config.

## Further Notes

- Unverified against a real `claude -p` run from the box's Windows `agent`
  account, to be confirmed the same way `agy`'s own quirks were: whether
  `stream-json`'s `system/api_retry` events actually appear for every quota-like
  condition in practice, whether a `--bare`-less session genuinely needs no
  further setup beyond the one interactive login, and whether the assembled
  prompt (the same one `agy` already receives) fits within whatever the `claude`
  CLI's own input limits are on Windows. A practice-run ticket, the same shape as
  the box-primary-worker effort's own practice run, is the natural place to
  confirm these before relying on the fallback overnight.
- Suggested ticket order (for `/to-tickets`):
  1. the `sonnet.py` adapter with recorded fixtures;
  2. `LocalWorker._start_with_fallback` and the four call-site changes, with
     `sonnet_driver` defaulting to `None`;
  3. the two new `LocalWorkerConfig` keys and `build_loop`'s wiring;
  4. a practice-run ticket (developer bench work, `ready-for-developer`, a leaf);
  5. a held, leaf documentation ticket noting the new adapter and config keys in
     `CONTEXT.md`'s *Local worker* and *Quota reserve* entries. No ADR change is
     needed — ADR 0006 and 0007 describe the box and `agy`'s permission model,
     neither of which this effort changes.
- This spec assumes the developer's Claude subscription plan is usable from a
  `claude` login on the box's `agent` Windows account. If that account needs a
  separate login from his main one, that's the same kind of one-time setup
  decision ADR 0007 already made for the box's Google login — his own account,
  the risk accepted, he is the only user of it.
