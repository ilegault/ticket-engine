# 48: The box shows agy is running, and never waits on agy forever

**Status:** in-progress

**Runner:** any

**Auto-merge:** yes

**Blocked by:** None (can start immediately)

**Spec:** none. Background is tickets 45–47 in this effort and the box's first real run.
**Binding:** ADR 0006; ADR 0007 rules 4 and 5 (agy's output and the box's logs stay in the box's local log, never posted, never committed)

## What to build

`box-worker` runs on the box as a windowless scheduled task. Its log file is the
only place anything it does can be seen. After `tick: claim owner/repo #NN` the log
goes silent until agy exits, which can be two hours. The box never logs that agy
started, and a checkpoint push that succeeds logs nothing. A run that is working
and a run that is hung look identical. The developer had to inspect processes and
file times on the box by hand to tell them apart.

`_default_run` in `src/ticket_engine/agy.py` also calls `subprocess.run` with no
timeout. It inherits stdin, decodes output with the Windows locale code page, and
returns `stdout or stderr`, which drops stderr whenever stdout has anything. If
agy hangs past its own `--print-timeout`, or waits on input, the box waits forever.

After this ticket:

- the log says when agy starts and in which worktree;
- every checkpoint push logs one line;
- agy gets no stdin and its output is decoded as UTF-8;
- the box kills agy, and every process agy started, 15 minutes after agy's own
  print timeout, and records that run as outcome `timeout`. The existing
  resume/escalation rules in `LocalWorker._resolve_outcome` then handle it unchanged.

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests may fake
agy with `AgyDriver(run_fn=...)` and git with `_make_git_runner` from
`tests/test_local_worker.py`. The hard-timeout test runs a real child process
(the current Python interpreter, `sys.executable`), never `agy`.

- [ ] **`print_timeout` is parsed, and an invalid one is refused.** Add a pure
  function `_duration_seconds(value: str) -> float` to `src/ticket_engine/agy.py`.
  It accepts one or more integer parts, each followed by `h`, `m` or `s`, in that
  order: `"2h"` → 7200, `"90m"` → 5400, `"1h30m"` → 5400, `"45s"` → 45. For
  anything else (`"7200"`, `""`, `"2 h"`, `"m5"`) it raises `ValueError` whose
  message contains the rejected value. `AgyDriver.__init__` gains a keyword
  parameter `hard_timeout_margin_s: float = 900.0` and sets
  `self.hard_timeout_s = _duration_seconds(self.print_timeout) + hard_timeout_margin_s`.
  An invalid `print_timeout` therefore makes the constructor raise, so the box
  refuses to start instead of running every session into agy's own rejection.
  Rewrite `test_agy_driver_start_custom_print_timeout` in `tests/test_agy_driver.py`
  **in place, same name**, using `"90m"` where it now uses `"3600"` (in the
  constructor and the expected args). New test
  `test_duration_seconds_accepts_units_and_rejects_bare_numbers` asserts every
  example above.
- [ ] **The default runner gives agy no stdin, decodes UTF-8, and keeps stderr.**
  Rewrite `_default_run` as `_default_run(args, cwd=None, timeout=None)` using
  `subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
  stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
  start_new_session=not _IS_WINDOWS)` and `proc.communicate(timeout=timeout)`.
  `_IS_WINDOWS = os.name == "nt"` is a module-level constant. The return value is
  unchanged: `(returncode, stdout)` when stdout is not blank, else
  `(returncode, stderr)`. In addition, when the return code is non-zero and both
  stdout and stderr are not blank, log
  `logger.warning("agy stderr: %s", stderr.strip()[-4000:])`. When `run_fn` is
  None, `AgyDriver.__init__` sets
  `self._run = functools.partial(_default_run, timeout=self.hard_timeout_s)`. The
  `RunFn` signature `(args, cwd)` that every test fake uses does not change.
  New test `test_default_run_returns_stdout_and_logs_stderr_on_failure`
  (caplog at WARNING): `_default_run([sys.executable, "-c", "import sys;
  print('out'); print('err-text', file=sys.stderr); sys.exit(3)"])` returns
  `(3, "out\n")` and a WARNING record contains `agy stderr: err-text`.
- [ ] **At the hard timeout, the whole process tree is killed and the run is a
  `timeout`.** On `subprocess.TimeoutExpired`, `_default_run` calls a new function
  `_kill_process_tree(proc)`. On Windows (`_IS_WINDOWS`) that runs
  `subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True, check=False)`.
  Elsewhere it runs `os.killpg(proc.pid, signal.SIGKILL)` and ignores
  `ProcessLookupError`. `_default_run` then calls `proc.communicate(timeout=30)` to
  collect what was printed, using empty strings if that also times out. It logs
  `logger.warning("agy killed after the %.0fs hard timeout", timeout)` and returns
  `(124, json.dumps({"status": "INTERRUPTED", "message": "killed by the box after the hard timeout"}))`.
  `_parse_agy_output` already classifies that as outcome `timeout`.
  New test `test_default_run_kills_a_child_and_its_grandchild_at_the_hard_timeout`,
  marked `pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")`: call
  `_default_run([sys.executable, "-c", script], timeout=1)`. `script` starts a
  grandchild `subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])`
  that inherits its stdout, then sleeps 60 s itself. Assert the call returns within
  15 s, the code is 124, and `_parse_agy_output(rc, out, [], []).outcome == "timeout"`.
  (Without the tree kill the grandchild holds the pipe open and the call takes
  more than 30 s, so this test fails.) New test
  `test_kill_process_tree_uses_taskkill_on_windows`: monkeypatch
  `ticket_engine.agy._IS_WINDOWS` to True and `ticket_engine.agy.subprocess.run` to
  a recorder. Call `_kill_process_tree` on an object whose `pid` is 4242, and
  assert the recorded args are `["taskkill", "/F", "/T", "/PID", "4242"]`.
- [ ] **The log says when agy starts.** In `AgyDriver.start`, immediately before
  calling `self._run`, log
  `logger.info("agy started in %s (print timeout %s)", cwd, self.print_timeout)`.
  Never log `prompt` or `args`. New test
  `test_agy_driver_logs_start_with_cwd_but_never_the_prompt` (caplog at INFO): the
  fake `run_fn` itself asserts that `caplog.text` already contains
  `agy started in C:/wt/x (print timeout 2h)` when it is called (proving the line
  is written before agy runs), then returns success JSON. Call
  `driver.start("PROMPT-MARKER-do-not-log", cwd="C:/wt/x")`, and after it
  returns, assert `"PROMPT-MARKER-do-not-log"` is not in `caplog.text`.
- [ ] **Every successful checkpoint push logs one line.** In
  `LocalWorker._push_branch` (`src/ticket_engine/local_worker.py`), when the push
  returns 0, log `logger.info("Pushed %s: %s", ticket_branch, last)`. `last` is the
  last non-blank line of the push output, or `"ok"` when the output is blank. The
  failed-push warning is unchanged. The output never carries the token, because
  `_make_push_env` passes it in an environment variable. New test
  `test_successful_push_logs_the_branch_and_git_summary` in
  `tests/test_local_worker.py` (caplog at INFO): use a git runner from
  `_make_git_runner` with `"push"` scripted to return
  `(0, "To https://github.com/o/r.git\n   c33b160..9a1f2e3  HEAD -> ticket/e-09-x\n")`
  and call `_push_branch("/wt", "ticket/e-09-x", None)` directly. Assert one INFO
  record contains both `Pushed ticket/e-09-x` and `c33b160..9a1f2e3`.
- [ ] **Existing tests unchanged.** Apart from the one in-place rewrite above, every
  existing test passes with its assertions as they are. No test is deleted,
  skipped or weakened.

## Gate

In CI order:

    ruff check .
    python scripts/check_tests_first.py
    pytest -q

## Out of scope

- `sonnet.py` and its runner.
- The box loop in `box_worker.py`, the box status issue and box alerts. Nothing
  new is posted to GitHub (ADR 0007 rule 4).
- `docs/box-setup.md`.
- `GitHubClient` logging (ticket 49) and PR refusals (ticket 50).

## Comments
