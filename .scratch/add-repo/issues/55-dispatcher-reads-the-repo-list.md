# 55: The dispatcher reads the repo list instead of `box_enabled`, and `jules_enabled = false` stops Jules

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 53, 54

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0009 rules 4 and 5; ADR 0010 rule 1; ADR 0006

## What to build

Today `LiveDispatcher.dispatch` (`src/ticket_engine/live_dispatch.py`, step "4d")
reads the box status issue only `if self.config.box_enabled:`. A repo with
`box_enabled = true` that the box does not work would stall forever. After this
ticket the dispatcher asks the repo list: the box covers this repo only when
`engine-repos.toml` on the engine's default branch lists it with `box = true`.
`box_enabled` is removed. Separately, a repo with `jules_enabled = false` never
gets a Jules session.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests fake
GitHub with the `MagicMock` clients `tests/test_live_dispatch.py` already uses;
the repo-list text is real TOML returned from a faked `get_file_contents`.
`DispatchCore` tests use real `WorldSnapshot`s as `tests/test_dispatch_core.py` does.

- [ ] **The box is read only for a listed repo.** In `live_dispatch.py`, add
  `LiveDispatcher._box_covers_repo(self) -> bool`: it calls
  `fetch_repo_list(self.github_client, ENGINE_REPO)` and returns True iff an entry's
  `repo` equals `self.repo` ignoring case and its `box` is True. Step 4d uses it in
  place of `self.config.box_enabled`. If fetching or parsing raises (`RepoListError`,
  `HTTPError`, `URLError`, `OSError`), log a WARNING and set `box = None`,
  `box_status_error = "repo list unreadable"`, exactly as an unreadable status
  issue is handled now. Rewrite **in place, same names**:
  - `test_live_dispatch_box_status_read_failure_sets_box_none_and_never_raises`
  - `test_live_dispatch_box_disabled_passes_no_box_and_makes_no_issues_request`
    (the repo is absent from the faked list; still asserts no issues request)
  - the third test that builds `RepoConfig(..., box_enabled=True)` (list the repo
    in the faked repo list instead).
  New test `test_live_dispatch_repo_list_box_false_means_no_box` (listed with
  `box = false` → `NO_BOX`, no issues request) and
  `test_live_dispatch_unreadable_repo_list_sets_box_none`.
- [ ] **`box_enabled` is gone.** Take the field out of `RepoConfig` and its line out of
  `load_repo_config`. `grep -rn box_enabled src tests` prints nothing. State the
  result under `## Comments`. A `.ticket-engine.toml` that still contains
  `box_enabled` loads without error (unknown keys are ignored); test
  `test_repo_config_ignores_retired_box_enabled`.
- [ ] **`jules_enabled = false` starts no Jules session.** In `DispatchCore.dispatch`
  (`src/ticket_engine/dispatch.py`), immediately after the "4b. Box available"
  block, when `not cfg.jules_enabled` return a `DispatchResult` with no
  `StartTicketAction`, built like the step-4 paused return
  (`skipped_windows_tickets=[t for t in frontier if t.runner == "windows"]`,
  `left_for_box=[]`, `box_state=box_state`), keeping every action already in
  `actions` (escalations, releases). Test
  `test_jules_disabled_repo_gets_no_start_even_when_box_paused`: a snapshot with
  one unclaimed lint-clean `Runner: any` frontier ticket and a box in
  `paused_quota` yields zero `StartTicketAction` with `jules_enabled=False`, and at
  least one with `jules_enabled=True` (same snapshot otherwise).
- [ ] **Existing tests unchanged.** Apart from the in-place rewrites above, every
  existing test passes with its assertions as they are. No test is deleted,
  skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- Not-ready repos and the developer pause (ticket 56).
- `.github/` workflows. The change reaches target repos when the `v1` tag moves
  (ticket 68).

## Comments
