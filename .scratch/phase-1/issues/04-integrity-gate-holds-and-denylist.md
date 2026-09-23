# 04: Integrity gate, hold rules and the denylist

**What to build:** The gate knows when a green PR must still wait for the developer, and it stops real names and IDs from reaching a public repo.

**Blocked by:** 03

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [x] Check 3: any ratchet file (paths from the repo config) with a higher value than on base → `fail`.
- [x] Check 4: a PR touching `.github/`, the gate scripts, `docs/adr/`, `AGENTS.md` or `CONTEXT.md` → `hold`, naming the paths.
- [x] Check 5: a tests-first escape hatch used (label or commit/PR tag, as the tests-first gate defines them) → `hold`.
- [x] A ticket with `Auto-merge: no` → `hold` regardless of other checks.
- [x] Denylist scan: every added line in the PR is checked against entries from the `PEOPLE_DENYLIST` secret; a hit → `fail` whose reason names the file and line but never echoes the matched entry.
- [x] When both `fail` and `hold` reasons exist the verdict is `fail`.
- [x] A fixture per rule proves it fires; a test proves a denylist match is never printed.

## Comments

Built Check 3 (ratchets), Check 4 (protected paths), Check 5 (escape hatches), Auto-merge hold, and People Denylist scan into pure `IntegrityCore` (`src/ticket_engine/integrity.py`) and adapter `integrity_runner.py`:
- Check 3: tests `test_check_3_ratchet_file_increased_fails`, `test_check_3_ratchet_file_equal_or_decreased_passes`, fixture `test_fixture_check3_ratchet_increased_fails`.
- Check 4: tests `test_check_4_touching_github_produces_hold`, `test_check_4_touching_adr_agents_context_or_gate_produces_hold`, fixture `test_fixture_check4_protected_path_holds`.
- Check 5: tests `test_check_5_tests_first_escape_label_produces_hold`, `test_check_5_commit_or_pr_tag_produces_hold`, fixture `test_fixture_check5_escape_hatch_holds`.
- Auto-merge hold: test `test_auto_merge_no_produces_hold`, fixture `test_fixture_automerge_no_holds`.
- Denylist scan: tests `test_denylist_scan_fails_and_never_prints_secret`, `test_denylist_removal_does_not_fail`, `test_run_integrity_gate_denylist_from_env_fails_without_leaking`, fixture `test_fixture_denylist_match_fails`. Verified that matched secret is never printed or logged.
- Precedence: tests `test_when_both_fail_and_hold_reasons_exist_verdict_is_fail`, fixture `test_fixture_fail_and_hold_fails`.

