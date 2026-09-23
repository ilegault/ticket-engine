# 02: The runner-agnostic ticket skill, and prompt assembly

**What to build:** One ticket skill replaces the three per-repo copies (rbl-ticket, tds-ticket, pbot-ticket), written so any worker can follow it, with a section for Jules. The engine can build the exact prompt a Jules session receives for a given ticket.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] One skill document in the engine, merged from the three existing skills, keeping every rule they share (orientation order, tests first, never mute, adversarial self-review, one ticket per branch, escalation) and dropping repo-specific commands in favour of "run the gate commands in the repo's engine config".
- [ ] No PowerShell-only commands and no hard dependency on `gh`; any command given is shown for both shells or is shell-neutral.
- [ ] A clearly marked "If you are a Jules worker" section: do not push, do not open or edit PRs, CI is watched for you, respond to red CI by fixing; the escalation brief format.
- [ ] The escalation brief format is specified: the ticket, what each of the three attempts tried, the exact failing output, the one decision needed — written to paste into a stronger model.
- [ ] The "refer to roles, never to people" rule is in the skill.
- [ ] A prompt-assembly function takes (skill text, repo, ticket path) and returns the prompt: skill, ticket path, and the instruction to read `AGENTS.md`, the ticket, its ADRs and `CONTEXT.md` first.
- [ ] Prompt assembly never logs the prompt; a test asserts nothing it produces is written to stdout/stderr.
- [ ] Tests assert on the assembled prompt's required contents for a sample ticket.

## Comments
