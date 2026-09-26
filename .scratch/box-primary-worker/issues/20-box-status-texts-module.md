# 20: Box status texts: one pure module for every public box text

**What to build:** A new pure module, `ticket_engine.box_status`, that owns every text the box or the engine posts publicly about the box. It renders and parses the box status issue body, and renders the box-alert and escalation-issue title and body from fixed templates. Typed inputs only, with no free-text field anywhere, and a test that every rendered line matches an allowlist. Later tickets (24, 26, 30, 31, 33) all use this module and never format these texts themselves. Spec: `.scratch/box-primary-worker/spec.md` (§Box status issue and alerts, §Escalation issues). ADR 0007 rule 4, ADR 0002.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Tests go in a new `tests/test_box_status.py`. Nothing is faked; the module is pure (no clock, no I/O, no logging of inputs). Write these tests first and watch them fail.

- [ ] **Types.** `BoxState` is a `str` enum with exactly `working`, `idle`, `paused_quota`, `paused_weekly_cap`, `login_expired`. `BoxStatus` is a frozen dataclass: `checked_in_at: datetime` (UTC, aware), `state: BoxState`, `current: TicketRef | None`, `paused_until: datetime | None`. `TicketRef` is a frozen dataclass `repo: str`, `number: int`. Its constructor raises `ValueError` unless `repo` matches `^[\w.-]+/[\w.-]+$`; a test passes `"a/b c"` and `"a/b\nx"` and expects `ValueError`.
- [ ] **Round trip.** `render_box_status(s)` returns the heading `## Box status`, then exactly these lines in order: `Checked in: <YYYY-MM-DDTHH:MMZ>`, `State: <state value>`, `Current: <owner/repo> #<NN>` or `Current: none`, `Paused until: <YYYY-MM-DDTHH:MMZ>` or `Paused until: none`. `parse_box_status(render_box_status(s)) == s` for every `BoxState`, with and without `current` and `paused_until` (seconds truncated to minutes on both sides). Assert one full rendered body with `==`.
- [ ] **Unreadable bodies.** `parse_box_status` returns `None` for `""`, for a body missing the `Checked in:` line, for an unknown state word, and for a malformed timestamp. It never raises.
- [ ] **Alerts and escalation issues.** `AlertKind` is a `str` enum `weekly_cap`, `login_expired`, `box_silent`. `render_box_alert(kind, owner, since)` returns `(title, body)`. The title is fixed per kind: `Box alert: weekly cap reached`, `Box alert: agy login expired`, `Box alert: box silent`. The body starts `@<owner>` and has one line `Since: <YYYY-MM-DDTHH:MMZ>`. `render_escalation_issue(ref, effort, title_slug, link, reason, owner)`, with `reason` a `str` enum `ci_failed`, `kept_asking`, `resumes_exhausted`, returns title `Escalation: <effort>-<NN> <title_slug>` and a body of `@<owner>`, `Ticket: <owner/repo> #<NN>`, `Link: <link>`, `Reason: <reason>`. `link` must start with `https://github.com/`, else `ValueError`. `owner` and `title_slug` must match `^[\w.-]+$`, else `ValueError`.
- [ ] **The public-text test.** `test_every_public_box_line_is_on_the_allowlist` renders every status state, every alert kind and every escalation reason. It asserts each line fully matches one regex from a module-level `PUBLIC_LINE_PATTERNS` tuple defined in the test file (not imported from the module under test). Adding a free-text parameter to any renderer and passing `"Traceback (most recent call last)"` through it must turn this test red; check this by hand once before landing. The module docstring's `WHY THIS EXISTS` cites ADR 0007 rule 4 and ADR 0002.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`, `pytest -q`.

## Comments
