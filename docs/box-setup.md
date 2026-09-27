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

Store the token only in the `agent` account's local config file (below). It
never goes in a commit, a log, or a status issue.

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

[agy]
print_timeout = "7200"
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

## Start at boot

Register a Windows Task Scheduler task, running as the `agent` account, that
starts at boot and launches `box-worker`. Configure it to restart on failure,
so a crash or a reboot resumes the loop without the developer's attention.

## Checking it works

From an `agent` session:

```
box-worker --once
```

`--once` runs a single pass of the loop and exits, instead of looping
forever — the fastest way to confirm the box, its config, and its GitHub
token all work before trusting it to the scheduled task. Check the box's
status issue on the engine repo for a fresh update, and confirm the Task
Scheduler task itself is enabled and set to run at startup.
