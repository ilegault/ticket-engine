# 02: The runner-agnostic ticket skill, and prompt assembly

**What to build:** One ticket skill replaces the three per-repo copies (rbl-ticket, tds-ticket, pbot-ticket), written so any worker can follow it, with a section for Jules. The engine can build the exact prompt a Jules session receives for a given ticket.

**Blocked by:** None (can start immediately)

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [x] One skill document in the engine, merged from the three existing skills, keeping every rule they share (orientation order, tests first, never mute, adversarial self-review, one ticket per branch, escalation) and dropping repo-specific commands in favour of "run the gate commands in the repo's engine config".
- [x] No PowerShell-only commands and no hard dependency on `gh`; any command given is shown for both shells or is shell-neutral.
- [x] A clearly marked "If you are a Jules worker" section: do not push, do not open or edit PRs, CI is watched for you, respond to red CI by fixing; the escalation brief format.
- [x] The escalation brief format is specified: the ticket, what each of the three attempts tried, the exact failing output, the one decision needed — written to paste into a stronger model.
- [x] The "refer to roles, never to people" rule is in the skill.
- [x] A prompt-assembly function takes (skill text, repo, ticket path) and returns the prompt: skill, ticket path, and the instruction to read `AGENTS.md`, the ticket, its ADRs and `CONTEXT.md` first.
- [x] Prompt assembly never logs the prompt; a test asserts nothing it produces is written to stdout/stderr.
- [x] Tests assert on the assembled prompt's required contents for a sample ticket.

## Comments

### Implementation & Verification Summary (2026-09-22)
- Created the runner-agnostic ticket skill in `.agents/skills/ticket/SKILL.md` (and packaged resource in `src/ticket_engine/resources/ticket_skill.md`), synthesized from `pbot-ticket`, `tds-ticket`, and `rbl-ticket`.
- Skill preserves orientation order (`AGENTS.md`, ticket file, ADRs, `CONTEXT.md`, module docstrings), test-first enforcement ("watch them fail"), zero test muting (no `skip`/`xfail`/weakened assertions), checkpointing at criterion boundaries, roles-not-people rule, and replaces repo-specific gates with repo engine config.
- Provides shell-neutral commands or both Bash and PowerShell alternatives, plus PR creation and CI watching without hard `gh` CLI dependencies.
- Added explicit "If you are a Jules worker" section with auto PR and watched CI directives, and standardized the escalation brief template for LLM handoffs.
- Implemented `assemble_prompt(skill_text, repo, ticket_path)` and `load_ticket_skill()` in `src/ticket_engine/prompt.py`. Verified zero stdout/stderr/log output.
- All 31 tests passing (`tests/test_prompt.py`, `tests/test_cli.py`, `tests/test_dispatch_core.py`, `tests/test_parser.py`).
