# Box setup runbook

How to rebuild the box — the always-on machine that runs `box-worker`, the
local worker — from a clean Windows install to a running worker. Follow ADR
0006 and ADR 0007 for the reasoning behind each step; this doc is the
checklist, not the argument.

## The agent account

Create a second, standard (non-admin) Windows user account named `agent`.
The developer's own account, and his personal file storage, stay on his
own account — `agent` is denied access to them (ADR 0007 rule 2). Everything
below runs as `agent`, not as the developer.

## Python and the engine checkout

Log in as `agent` and install Python 3.12+. Clone the engine repo and
install it in editable mode alongside its dev tools:

```
git clone https://github.com/ilegault/ticket-engine.git
cd ticket-engine
python -m venv .venv
.venv\Scripts\activate
pip install -e . ruff pytest
```

A fresh account blocks PowerShell scripts, so `activate` fails until you run
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once as `agent`.

Clone every target repo the box should work, also as `agent` (for example
under `C:\Users\agent\projects\`). These are the paths the `[[repos]]`
blocks below point at. Run one `git pull` in each by hand: if git asks for a
login, complete it now, because the scheduled task has no window to show it
in. Never edit or commit inside these clones by hand; the box pulls them with
`--ff-only` on every tick, and a local commit makes that pull fail.

## agy and Claude logins

Run `agy` once interactively, under the `agent` account, and complete its
login flow. This is the one-time interactive step ADR 0007 calls out — every
run after this is headless.

If the Sonnet quota fallback is enabled (below), also run `claude` once
interactively, under the same `agent` account, and complete its login flow.
This draws on the developer's own Claude subscription plan — no API key, no
`--bare` — and, like `agy`'s login, is a one-time interactive step; every
headless `claude -p` run after this uses the same login.

## GitHub token

Create a fine-grained personal access token, scoped to:

- The target repos: **Contents** (read/write), **Pull requests** (read/write),
  **Issues** (read/write), and **Variables** (read, so the box can honour
  `TICKET_ENGINE_PAUSED`).
- The engine repo: **Issues** (read/write) only.
- No **Workflows** permission and no **Administration** permission on any repo
  (ADR 0007 rule 3).
- An expiration date, never "no expiration".

A fine-grained token applies one permission set to every repo it covers, so
the engine repo ends up with the same permissions as the target repos. That
is acceptable only while the engine repo's default branch accepts changes
through pull requests alone.

Use a separate token from the one in the target repos' `PIPELINE_TOKEN`
Actions secret, so the box's token can be revoked on its own.

Store the token only in the `agent` account's local config file (below), as
`github_token`. The box falls back to a `PIPELINE_TOKEN` environment variable
only when the config has none; environment variables do not reliably reach a
scheduled task, so the config file is the one to rely on. The token never
goes in a commit, a log, or a status issue.

## Local config

The box reads `~/.ticket-engine-local.toml` (`agent`'s home directory), in
the layout `load_local_config` parses:

```toml
worktree_base = ""
checkpoint_push_minutes = 20
max_resumes_per_ticket = 3
concurrency = 1
poll_interval_minutes = 10
status_interval_minutes = 30
weekly_cap_after_hours = 5
weekly_cap_backoff_hours = 12
engine_repo = "ilegault/ticket-engine"
logs_dir = "C:/Users/agent/ticket-engine-box/logs"
sonnet_enabled = false
sonnet_timeout_seconds = 7200
git_timeout_seconds = 300

[agy]
print_timeout = "2h"
quota_error_patterns = ["quota", "rate limit", "exhausted"]
auth_error_patterns = ["auth", "login", "credential"]

[[repos]]
path = "C:/Users/agent/projects/target-repo"
repo = "owner/target-repo"
```

`github_token` is deliberately left out of this example — paste the real
token, from the previous section, into this file directly on the box.
`repos` lists every target repo clone the box should work; add one `[[repos]]`
block per repo.

`print_timeout` is a duration with a unit (`2h`, `90m`). agy rejects a bare
number such as `7200`, and every session then fails within seconds. Save the
file as `.ticket-engine-local.toml` exactly. Notepad appends `.txt` and
Explorer hides it, and a missing config makes the box run with no repos.
Check with `Get-ChildItem C:\Users\agent -Force -Filter ".ticket-engine-local*"`.

## Start at boot

Register a Windows Task Scheduler task, running as the `agent` account, that
starts at boot and launches `box-worker`. Configure it to restart on failure,
so a crash or a reboot resumes the loop without the developer's attention.

- Give `agent` a password first: Windows will not run a task for a
  passwordless account while nobody is logged on. Set it from inside the
  `agent` account (Ctrl+Alt+Del, Change a password), not as an admin reset,
  so `agent`'s saved agy and Claude logins survive.
- Create the task from the developer's admin account (Task Scheduler, Run as
  administrator), with `agent` chosen under Change User or Group. A standard
  account cannot register a task that runs while logged off. If Windows says
  the account needs "Log on as a batch job", grant it in `secpol.msc` under
  Local Policies, User Rights Assignment.
- General: "Run whether user is logged on or not", password stored, not
  "Run with highest privileges", Configure for Windows 10.
- Trigger: At startup. Action: `C:\Users\agent\ticket-engine\.venv\Scripts\box-worker.exe`,
  Start in `C:\Users\agent\ticket-engine`.
- Settings: restart on failure every 5 minutes, and untick "Stop the task if
  it runs longer than".

## Checking it works

From an `agent` session:

```
box-worker --once
```

`--once` runs a single pass of the loop and exits, instead of looping
forever — the fastest way to confirm the box, its config, and its GitHub
token all work before trusting it to the scheduled task. Check the box's
status issue on the engine repo for a fresh update (its `Checked in:` time is
UTC), and confirm the Task Scheduler task itself is enabled and set to run at
startup.

The box's log, `box-worker.log` under `logs_dir`, gets one `tick: …` line per
tick. A recent `tick:` line means the loop is alive; `tick failed` lines carry
the traceback of whatever stopped it, and `git pull failed` warnings name a
target clone the box could not update.
