# ADR 0004 — The engine answers a waiting worker; it never waits on the developer

**Status:** accepted
**Date:** 2026-09-25
**Applies to:** the dispatcher, the Jules adapter, and the ticket skill

## Context

Jules sessions are created with plan approval off (`requirePlanApproval: false`),
and every prompt carries the unattended-run rule from `prompt.py`: nobody is
watching, never ask for confirmation, escalate instead. On Slackbot ticket 46 a
Jules worker stopped mid-ticket anyway and asked whether its progress looked right.
The session sat in `AWAITING_USER_FEEDBACK`.

Jules has no setting that forbids the agent from asking. Rewording the prompt only
makes it less likely. Meanwhile the dispatcher counted the waiting session as live,
so the ticket's claim was never released. The ticket, and everything behind it,
waited all night for an answer nobody was going to give.

## Decision

1. **Every dispatch run answers this repo's waiting sessions, and so does a paused
   run.** A paused repo starts nothing new. Work already in flight still finishes, and
   finishing may need an answer.
2. **The answer is fixed text** (`dispatch.AUTO_REPLY_TEXT`): nobody is watching,
   decide from the ticket, ADRs and `AGENTS.md`, finish, or escalate if truly blocked.
   It is sent through the Jules API's `sessions/<id>:sendMessage`.
3. **At most `max_auto_replies` answers per session** (engine config, default 2). If
   the session asks again after the last one, the engine escalates. It commits
   `Status: blocked` and an escalation brief to the ticket file on the ticket's
   **claim branch**, then sends a stop message. The claim branch is used because no
   PR exists yet, and the default branch is protected. The claim stays, so the
   ticket stays off the frontier until the developer acts.
4. **No state of its own.** Every message the engine sends starts with a marker
   (`[ticket-engine auto-reply]` or `[ticket-engine escalated]`). The reply count and
   the escalated flag are read back from the session's activity list on each run.
   The engine sends nothing when:
   - the session's newest activity is its own reply (the session has not read it
     yet);
   - its history could not be fetched (the reply count is unknown).
5. **The agent's question is never copied into the brief or the run report.** It is
   an API response body, and the brief lands in a public repo (ADR 0002). The brief
   points at the session in the Jules web UI instead.
6. **The ticket skill never invites a question.** It says the worker is unattended.
   It records decisions under `## Comments` instead of announcing them, and it lists
   asking for confirmation under **Never**.

## Consequences

- A question from Jules costs one dispatch interval (hourly, or the next push)
  instead of a night.
- An escalated waiting session holds a claim, and so a concurrency slot, until the
  developer answers it in Jules or reworks the ticket and deletes the claim branch.
  This is the same cost an escalated PR has. The run report names it on every run.
- The circuit breaker does not yet count these escalations, because the live
  dispatch path does not feed it escalations of any kind. The morning report does
  not show them yet either. The run report is the surface for now.
- If the stop message fails after the escalation commit lands, the next run
  escalates again and the brief is written twice. That is harmless and visible.
- `AWAITING_PLAN_APPROVAL` is not handled. Plan approval is off at session creation,
  so a session should never reach that state.
