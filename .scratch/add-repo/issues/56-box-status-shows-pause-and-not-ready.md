# 56: Box status shows a developer pause and not-ready repos; the dispatcher lets Jules cover them

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** 55

**Spec:** `.scratch/add-repo/spec.md`
**Binding:** ADR 0010 rules 4–6; ADR 0007 rule 4 (fixed-template text only); ADR 0006

## What to build

The pure pieces the box (tickets 58, 61, 62) and the dispatcher need to talk about
two new situations: the developer paused the box, and the box will not work a
listed repo (*not ready*). Both mean "the box is not covering this", so the
dispatcher treats them like a paused box and Jules may overflow onto `Runner: any`
tickets. All text stays fixed-template.

## Acceptance criteria

Write the tests first in `tests/test_box_status.py` and
`tests/test_dispatch_core.py`, and watch each fail before changing `src/`. No
fakes are needed: everything here is pure.

- [ ] **New state and reasons.** In `src/ticket_engine/box_status.py`: add
  `BoxState.paused_by_developer = "paused_by_developer"`; add
  `class NotReadyReason(str, Enum)` with members `clone_failed`, `no_token_access`,
  `no_engine_config`, `env_failed`, `baseline_red`; add
  `@dataclass(frozen=True) class NotReady` (`repo: str`, `reason: NotReadyReason`)
  whose `__post_init__` validates `repo` with `_REPO_RE` and coerces a string
  `reason` the way `BoxStatus` coerces `state`. Export all three in `__all__`.
- [ ] **The status issue lists not-ready repos.** `BoxStatus` gains
  `not_ready: tuple[NotReady, ...] = ()`. `render_box_status` appends one line
  `Not ready: owner/a (baseline_red), owner/b (env_failed)` after `Paused until:`,
  in the tuple's order, **only when the tuple is non-empty**, so every existing
  exact-body test passes unchanged. `parse_box_status` reads the line when present
  (missing → `()`; a malformed entry → returns None). Tests
  `test_render_box_status_lists_not_ready_repos` (exact body),
  `test_parse_box_status_round_trips_not_ready`,
  `test_parse_box_status_without_not_ready_line_gives_empty_tuple`,
  `test_parse_box_status_malformed_not_ready_returns_none`.
- [ ] **A per-repo not-ready alert.** New
  `render_repo_not_ready_alert(repo: str, reason: NotReadyReason | str, owner: str, since: datetime.datetime) -> tuple[str, str]`:
  title `Box alert: <repo> not ready`, body `@<owner>\nReason: <reason>\nSince: <YYYY-MM-DDTHH:MMZ>\n`.
  It validates `repo` with `_REPO_RE`, `owner` with `_IDENT_RE`, and `reason`
  against the enum, raising `ValueError` otherwise. Test
  `test_render_repo_not_ready_alert_exact_text_and_rejects_free_text` (a reason of
  `"Traceback ..."` raises).
- [ ] **The dispatcher counts both as paused.** `classify_box` in
  `src/ticket_engine/dispatch.py` gains a keyword parameter `repo: str | None = None`.
  It returns `"paused"` for `BoxState.paused_by_developer`, and, when the box would
  otherwise be `"available"`, returns `"paused"` if `repo` matches a
  `box.not_ready` entry ignoring case. The `WorldSnapshot` built in step 5 of
  `LiveDispatcher.dispatch` (`live_dispatch.py`) gets `repo_name=self.repo`, and
  `DispatchCore.dispatch` calls
  `classify_box(snapshot.box, now, cfg.box_silent_hours, repo=snapshot.repo_name or None)`.
  The morning report's call is unchanged. Tests
  `test_classify_box_paused_by_developer_is_paused`,
  `test_classify_box_not_ready_repo_is_paused_other_repo_available`,
  `test_dispatch_starts_jules_on_a_not_ready_repo` (box `working`, this repo in
  `not_ready` → at least one `StartTicketAction`).
- [ ] **Existing tests unchanged.** Every existing test passes with its assertions
  as they are. No test is deleted, skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- The box writing these states (tickets 58, 61, 62).
- The morning report's wording for the new state; it already shows `box_state`.

## Comments
