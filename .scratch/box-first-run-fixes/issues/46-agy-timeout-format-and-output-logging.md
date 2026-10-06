# 46: agy gets a valid timeout, and its output reaches the box's log

**What to build:** On the box's first real run every agy session exited within
seconds. The box had passed `--print-timeout 7200`, and agy rejects that: it takes
a duration with a unit, as its own default `5m` does (`invalid value "7200" for
flag --print-timeout`). The box never logged agy's output, so the failure was
invisible. It only showed up when the developer ran agy by hand. That same manual run, with
`--print-timeout 2h`, produced the real headless JSON shape recorded below. It
carries `status` as the code expects, but names its session `conversation_id`,
which `_parse_agy_output` does not read.

Make the default timeout a valid duration, log every non-success agy result's
output to the box's local log, read `conversation_id`, and pin the parser to the
recorded real output.

**Blocked by:** 45

**Status:** ready-for-agent

**Runner:** any

**Auto-merge:** yes

## Acceptance criteria

Write the tests first and watch each fail before changing `src/`. Tests fake agy
with `AgyDriver(run_fn=...)`. The parser and `load_local_config` are real.

- [ ] **Default `print_timeout` is `"2h"`.** In `src/ticket_engine/local_config.py`,
  change the three `"7200"` defaults for `print_timeout` to `"2h"`: the dataclass
  field, the docstring's TOML example, and the `agy.get("print_timeout", ...)`
  fallback. In `src/ticket_engine/agy.py`, change `AgyDriver.__init__`'s
  `print_timeout` default to `"2h"`. In the module docstring, replace
  `(default 7200 seconds)` with `(a duration with a unit, e.g. "2h"; agy rejects a
  bare number of seconds)`. Rewrite `test_load_local_config_missing_file_returns_defaults`
  in `tests/test_local_worker.py` **in place, same name**, so that it asserts
  `config.print_timeout == "2h"`. Leave `sonnet_timeout_seconds` alone (an
  integer, a different CLI).
- [ ] **`docs/box-setup.md` matches.** In the `## Local config` TOML example,
  change `print_timeout = "7200"` to `print_timeout = "2h"`. Directly after the
  paragraph that begins ``"`github_token` is deliberately left out"``, add this
  paragraph verbatim:
  ```
  `print_timeout` is a duration with a unit (`2h`, `90m`). agy rejects a bare
  number such as `7200`, and every session then fails within seconds. Save the
  file as `.ticket-engine-local.toml` exactly. Notepad appends `.txt` and
  Explorer hides it, and a missing config makes the box run with no repos.
  Check with `Get-ChildItem C:\Users\agent -Force -Filter ".ticket-engine-local*"`.
  ```
  `tests/test_box_setup_doc.py` must still pass unchanged.
- [ ] **Recorded real output parses as success.** Add
  `tests/fixtures/agy/success_recorded.json` containing exactly this one line, the
  developer's recorded `agy -p ... --output-format json` output:
  ```
  {"conversation_id":"0614ac83-77db-445a-beb6-c0221aaa2a2f","status":"SUCCESS","response":"Hi! How can I help you today?\n","duration_seconds":5.12173,"num_turns":1,"usage":{"input_tokens":17723,"output_tokens":161,"thinking_tokens":152,"cache_read_tokens":0,"total_tokens":17884}}
  ```
  In `_parse_agy_output` (`src/ticket_engine/agy.py`), change the session line to
  `session_id = raw.get("session_id") or raw.get("conversation_id") or raw.get("id")`.
  New test `test_agy_driver_parses_recorded_real_success_output` in
  `tests/test_agy_driver.py`: a `run_fn` returning `(0, <fixture text>)` gives
  `result.outcome == "success"`, `result.success is True` and
  `result.session_id == "0614ac83-77db-445a-beb6-c0221aaa2a2f"`.
- [ ] **A non-success result is logged with agy's output; the prompt never is.**
  In `AgyDriver.start`, after `_parse_agy_output` returns, log before returning the
  result:
  - on success: `logger.info("agy finished: outcome success, exit %d", returncode)`;
  - otherwise: `logger.warning("agy finished: outcome %s, exit %d, output: %s", result.outcome, returncode, output.strip()[-4000:])`.

  Never log `prompt` or `args`. This logger reaches only the box's local rotating
  file and the local `work-windows` console. `AgyDriver` never runs in Actions, so
  ADR 0007 rule 4 holds. Say so in one sentence added to the module docstring.
  New test `test_agy_driver_logs_failed_output_but_never_the_prompt` in
  `tests/test_agy_driver.py`, using `caplog` at `WARNING`: `run_fn` returns
  `(1, 'Error: invalid value "7200" for flag --print-timeout')`, and the prompt is
  `"PROMPT-MARKER-do-not-log"`. Assert that one WARNING record's message contains
  `outcome failed`, `exit 1` and `invalid value "7200"`, and that
  `"PROMPT-MARKER-do-not-log"` is not in `caplog.text`.
- [ ] **Existing tests unchanged.** Apart from the one in-place rewrite above, every
  existing test passes with its assertions as they are. No test is deleted, skipped
  or weakened.

Gates, in CI order: `ruff check .`, `python scripts/check_tests_first.py`,
`pytest -q`.

## Out of scope

- `sonnet.py` and its output logging.
- Making `load_local_config` fail loudly on a missing or unparseable file. That is
  a separate box-startup change.
- `local_worker.py` and the box loop: ticket 45 owns the path fix.

## Comments
