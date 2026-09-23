# 09: Local worker for Windows tickets

**What to build:** One command on the developer's machine picks up `Runner: windows` tickets across their repos and works them with the Antigravity CLI, with the same claims, gate and quota care as the cloud worker. In Phase 2 the same command runs as a scheduled task on the Windows mini PC.

**Blocked by:** 02, 06

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [x] A local config lists the developer's local clones; the command computes each repo's frontier restricted to `windows` tickets.
- [x] It claims one ticket via the same claim-branch mechanism and works in a separate git worktree, never the developer's checkout.
- [x] It drives `agy -p <assembled prompt> --output-format json`.
- [x] Before starting, it reads remaining Antigravity quota from the local status endpoint when available, and refuses to start below 20%; if the endpoint is unavailable it proceeds and relies on quota-error detection.
- [x] The skill instructs the worker to checkpoint at acceptance-criterion boundaries: commit and push WIP to the ticket branch plus a progress note of five lines or fewer under `## Comments`.
- [x] On a quota error it keeps the claim, waits until the reported reset, then resumes with `agy --continue`; if that fails, a fresh session is started from the checkpoint and progress note.
- [x] The resulting PR goes through the normal integrity gate.
- [x] Tests use a fake `agy`: start, quota refusal, quota stop then resume, resume fallback to fresh session.

## Comments

Built `LocalWorker` orchestrator (local_worker.py), `AgyDriver` adapter (agy.py), `LocalWorkerConfig` (local_config.py), and `work-windows` CLI entry point. 27 new tests cover all 8 ACs using injectable fakes. Also fixed a pre-existing bug in `integrity_runner.py` where `execute_new_tests_on_base` used `["pytest"]` directly on Windows (silently fails); now always uses `[sys.executable, "-m", "pytest"]`. Gate: ruff ✓, check_tests_first ✓, 154 pytest ✓.
