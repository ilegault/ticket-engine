# Domain docs

How engineering skills should consume this repo's domain documentation.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root — the vocabulary of ticket-engine: engine vs
  target repo, ticket, effort, status, frontier, claim, worker, verdict, merge hold,
  escalation, circuit breaker. If a term here and the code disagree, flag it; do not
  silently pick a side.
- **`docs/adr/`** — read the ADRs that touch the area you are about to work in.
  They are binding, not background.

If any of these files do not exist, proceed silently.

## File structure

```
/
├── AGENTS.md          conventions + the ACTIVE-PLAN pointer
├── CLAUDE.md          one line: @AGENTS.md
├── CONTEXT.md         the glossary
├── engine-repos.toml  target repos the morning report covers
├── docs/adr/          binding decisions
├── docs/agents/       how agents work this repo
├── .scratch/          specs and tickets, tracked in git
├── scripts/           gate script, morning-report runner
└── src/ticket_engine/ cores, adapters, orchestrators, resources/ticket_skill.md
```

## Use the glossary's vocabulary

When your output names a domain concept, use the term as `CONTEXT.md` defines it.
The five status words are exact: `ready-for-agent`, `ready-for-developer`,
`in-progress`, `blocked`, `done`. Never `complete`, `completed` or `human-task`.

If a concept is not in the glossary: either you are inventing language the engine
does not use (reconsider), or there is a real gap (add it to `CONTEXT.md` as part of
the task).

## Flag ADR conflicts

If your output contradicts an ADR, surface it explicitly rather than silently
overriding:

> _Contradicts ADR 0003 decision 1 (workers start from the default branch), but worth reopening because…_
