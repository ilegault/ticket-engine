# 21: Every claim records its worker with a Claimed-by line

**What to build:** A ticket file can carry a `Claimed-by:` line (`box` or `jules`) directly under `Status:`. The parser reads it, the live dispatcher writes `Claimed-by: jules` onto the ticket file on each claim branch it creates, and every worker prompt tells the worker to keep that line, so the merged PR records who did the ticket. Status words are unchanged. Spec: `.scratch/box-primary-worker/spec.md` (§Dispatcher changes, Claimed-by). ADR 0006 rule 4.

**Blocked by:** None (can start immediately)

**Status:** done

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in `tests/test_parser.py`, `tests/test_live_dispatch.py` and `tests/test_prompt.py`. The parser and `assemble_prompt` are real. The GitHub and Jules clients are the fakes `tests/test_live_dispatch.py` already uses. Write these tests first and watch them fail.

- [x] **Parser.** `Ticket` gains `claimed_by: str = ""`. `TicketParser` reads `**Claimed-by:** box` (with or without the bold markers, the same regex shape as `_RUNNER_RE`) into `claimed_by == "box"`, and `jules` likewise. A missing line gives `""`. Any other value gives `""` plus a `ParseFinding` naming the value. A ticket with an unknown `Claimed-by` is still `is_done()` when its status is `done`: the line is metadata, not status. Four tests, one per case.
- [x] **Lint ignores it.** `lint_ticket` returns `[]` for an otherwise clean ticket carrying `**Claimed-by:** box`.
- [x] **The dispatcher writes it.** In `LiveDispatcher.dispatch`, right after `create_claim_branch` succeeds and before `create_session`, the ticket file on the claim branch is committed with `**Claimed-by:** jules` inserted on the line after the `Status:` line. Use `github_client.get_file_contents` and `commit_file_change` exactly as `_handle_waiting_sessions` does for its escalation commit. The commit message is `Claim <NN> for jules`. The test asserts the fake's recorded committed content contains `**Status:** ready-for-agent\n\n**Claimed-by:** jules` (or the file's own Status spelling followed by the new line), and that this commit is recorded before the session is created. If the commit fails, the session is still created and the failure is logged. A test covers that path too.
- [x] **Workers keep it.** `_final_step_rule` gains the sentence `Keep the ticket's Claimed-by: line exactly as it is.` A test asserts `assemble_prompt(...)` contains that sentence.
- [x] **Docstrings.** `parser.py`'s `WHY THIS EXISTS` names ADR 0006 rule 4 for the new line. Removing the claim-branch commit turns the dispatcher test red; check this by hand once before landing.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments

2026-09-26: Implemented Claimed-by recording on claim branches and worker prompts per ADR 0006 rule 4.
- AC1: Ticket gains `claimed_by: str = ""`; TicketParser parses `Claimed-by` ('box'/'jules') and emits ParseFinding on unknown values without blocking is_done; tested in `test_parser_reads_claimed_by_box`, `test_parser_reads_claimed_by_jules`, `test_parser_missing_claimed_by_defaults_to_empty`, `test_parser_unknown_claimed_by_yields_finding_and_preserves_is_done`.
- AC2: `lint_ticket` ignores `Claimed-by: box`; tested in `test_lint_ignores_claimed_by_box`.
- AC3: `LiveDispatcher.dispatch` commits `Claimed-by: jules` to claim branch after creation and before session launch; handles failures gracefully; tested in `test_live_dispatch_writes_claimed_by_jules_to_claim_branch` and `test_live_dispatch_creates_session_even_if_claimed_by_commit_fails`.
- AC4: `_final_step_rule` in `prompt.py` instructs workers to keep Claimed-by line as is; tested in `test_assemble_prompt_tells_workers_to_keep_claimed_by_line`.
- AC5: `parser.py` docstring updated with ADR 0006 rule 4. Adversarial check confirmed removing commit_file_change turns dispatcher test red. Full test suite passes with 392 tests green.
