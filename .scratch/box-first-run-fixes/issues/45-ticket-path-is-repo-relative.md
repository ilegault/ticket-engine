# 45: The box uses the ticket file's repo-relative path everywhere

**What to build:** On its first real run the box claimed Slackbot ticket 98, then
every GitHub read and write of the ticket file returned 404, `Claimed-by: box` was
never written, agy's prompt pointed at the box's main clone instead of the
worktree, and the escalation wrote its brief into the main clone. Nothing was
committed on the ticket branch, so `create_pull_request` returned 422 ("No commits
between master and ticket/...") and the loop crashed.

One cause: `local_worker._load_tickets_from_path` parses each ticket with an
**absolute** path (`C:/Users/agent/projects/Slackbot/.scratch/...`), and
`local_worker._ticket_path_str` returns `ticket.path` with only `\` turned into
`/`. Every caller treats that string as repo-relative: the GitHub contents API
(`_commit_claimed_by`, `_claim_still_mine`, `list_box_claims`), the prompt
(`assemble_prompt`), `pathlib.Path(worktree_path) / ticket_path` (which returns
the absolute path unchanged), and `git -C <worktree> add <ticket_path>`. The
existing tests never caught it because `make_ticket` builds tickets with no
`path`, so `_ticket_path_str` always took its relative fallback.

Fix `_ticket_path_str` so it always returns the repo-relative, `/`-separated
path, and prove it through the real loader. No caller changes.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Write the tests first, in `tests/test_local_worker.py`, and watch each one fail
against the current `_ticket_path_str` before changing it. Tests fake the GitHub
client (`MagicMock`, as `make_fake_github` does), agy (`AgyDriver(run_fn=...)`) and
git (`_make_git_runner`). The ticket loader and parser must be real:
`_load_tickets_from_path` over a ticket file written under pytest's `tmp_path`.

- [ ] **`_ticket_path_str` returns the repo-relative path.** In
  `src/ticket_engine/local_worker.py`, replace the body of `_ticket_path_str` with
  exactly this, and give it this docstring:
  ```python
  def _ticket_path_str(ticket: Ticket, effort: str) -> str:
      """The ticket file's path relative to the repo root, `/`-separated.

      GitHub's contents API, the worker prompt, and `git -C <worktree> add` all
      need this form. `Ticket.path` from `_load_tickets_from_path` is absolute
      (the box's clone), so keep only the part from the last `.scratch` on.
      """
      if ticket.path:
          parts = str(ticket.path).replace("\\", "/").split("/")
          if ".scratch" in parts:
              idx = len(parts) - 1 - parts[::-1].index(".scratch")
              return "/".join(parts[idx:])
      return f".scratch/{effort}/issues/{ticket.number:02d}-{ticket.slug}.md"
  ```
  The logic is pure string handling on purpose, so a Windows path is handled the
  same way on Linux CI.
  New test `test_ticket_path_str_strips_an_absolute_windows_path`: a `Ticket` with
  `path=pathlib.Path(r"C:\Users\agent\projects\Slackbot\.scratch\e\issues\50-x.md")`
  returns exactly `".scratch/e/issues/50-x.md"`. A second assertion in the same
  test: a ticket with `path=None`, `effort="e"`, `number=7`, `slug="y"` returns
  `".scratch/e/issues/07-y.md"`.
- [ ] **`run_one` sends GitHub the relative path.** New test
  `test_run_one_uses_repo_relative_ticket_path_with_a_loaded_ticket`: write
  `.scratch/e/issues/50-x.md` under `tmp_path` (a `ready-for-agent` ticket with
  `**Runner:** any` and one `- [ ]` criterion), load it with
  `_load_tickets_from_path(tmp_path)`, and call
  `worker.run_one(make_repo_entry(path=str(tmp_path)), ticket)` with agy returning
  `(0, '{"status": "SUCCESS"}')`. Assert that every `get_file_contents` call's path
  argument and the `commit_file_change` call's `path=` keyword are exactly
  `".scratch/e/issues/50-x.md"`.
- [ ] **The prompt names the relative path.** In the same scenario, the fake
  `run_fn` records the args it received. Assert that the prompt (the argument after
  `-p`) contains `.scratch/e/issues/50-x.md` and does **not** contain
  `str(tmp_path)`.
- [ ] **Escalation writes into the worktree and commits.** New test
  `test_escalation_writes_brief_into_the_worktree_copy_of_a_loaded_ticket`: same
  loaded ticket. Copy the setup of `test_resumes_exhausted_escalates_with_all_five_effects`
  (agy always returns `(1, '{"status": "ERROR", "message": "boom"}')`,
  `max_resumes_per_ticket=3`), but have `write_ticket_fn` record the path it is
  given. Take the worktree path from the `git worktree add` call
  (`_extract_worktree_path`). Assert:
  - the recorded write path equals
    `pathlib.Path(<worktree path>) / ".scratch/e/issues/50-x.md"`;
  - the escalation's `git ... add` call's last argument is exactly
    `".scratch/e/issues/50-x.md"`;
  - `create_pull_request` was called once with `draft=True`.

  This proves the escalation commit lands on the ticket branch, so an escalated
  ticket's branch is never empty. That is what caused the 422. Do not add a
  `try/except` around `create_pull_request`.
- [ ] **Existing tests unchanged.** Every existing test in `tests/` still passes
  with its assertions as they are. No test is deleted, skipped or weakened.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`,
`pytest -q`.

## Out of scope

- `_load_tickets_from_path` and `TicketParser`: leave `Ticket.path` absolute.
  `parser.py` reads `path.parts` for the effort.
- `agy.py`, `local_config.py` and `docs/box-setup.md`: ticket 46.
- The box loop (`box_worker.py`, `box_core.py`): unchanged.

## Comments
