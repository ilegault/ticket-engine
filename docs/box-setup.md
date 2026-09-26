# Box setup

How the developer rebuilds the box — the Windows mini PC that runs the local
worker permanently (ADR 0006) — from a clean Windows install to a running
`box-worker`, as the code stands after ticket 31.

## The agent account

Create a standard (non-admin) Windows user named `agent`. The developer's own
account, and the personal storage folder in it, must be denied to `agent`
(ADR 0007 rule 2): a runaway agent process can then damage only its own
account, never the developer's files.

Do all of the following steps logged in as `agent`, not as the developer.

## Python and the engine checkout

1. Install Python 3.12 or later for the `agent` account.
2. Clone this repo (or the target repo, for a box dedicated to one repo) to a
   folder under the `agent` account's profile, e.g.
   `C:\Users\agent\ticket-engine-box\repos\<name>`.
3. From that checkout: `pip install -e .` installs the `box-worker`,
   `dispatch`, `integrity-gate` and `work-windows` console scripts.
4. Repeat the clone for every target repo the box should work.

## agy login

Run `agy` interactively, once, under the `agent` account, and complete its
login flow. This is the one interactive step in this whole runbook — after
it, `box-worker` drives `agy` headlessly
(`--dangerously-skip-permissions`, ADR 0007 rule 1) and never prompts again.
The `agy` quota belongs to whichever account performs this login.

## GitHub token

Create a fine-grained personal access token, expiring, scoped to:

- **Target repos:** Contents (read/write), Pull requests (read/write), Issues
  (read/write), and Variables (read) — the last one lets the box see
  `TICKET_ENGINE_PAUSED` (ADR 0007 amendment, rule 3).
- **Engine repo:** Issues (read/write) only.
- No Workflows permission, no Administration permission, on either.

Store the token as the `PIPELINE_TOKEN` environment variable in the `agent`
account (or as `github_token` in the local config below) — never committed,
never pasted into a ticket, an issue, or this doc.

## Local config

`box-worker` reads a TOML file, by default `~/.ticket-engine-local.toml` under
the `agent` account (override with `--config <path>`). Every field of
`LocalWorkerConfig` besides `repos` and `github_token` (which come from the
GitHub token step above) has a default; a box running more than one repo lists
each as its own `[[repos]]` table.

```toml
worktree_base = "C:/Users/agent/ticket-engine-box/worktrees"
checkpoint_push_minutes = 20
max_resumes_per_ticket = 3
concurrency = 1
poll_interval_minutes = 10
status_interval_minutes = 30
weekly_cap_after_hours = 5
weekly_cap_backoff_hours = 12
engine_repo = "ilegault/ticket-engine"
logs_dir = "C:/Users/agent/ticket-engine-box/logs"

[agy]
print_timeout = "7200"
quota_error_patterns = ["quota", "rate limit", "exhausted"]
auth_error_patterns = ["auth", "login", "credential"]

[[repos]]
path = "C:/Users/agent/ticket-engine-box/repos/slackbot"
repo = "ilegault/slackbot"
```

## Start at boot

Create a Windows Task Scheduler task, running as the `agent` account:

- **Trigger:** at startup.
- **Action:** run `box-worker` (no arguments; it loops forever, ticking every
  `poll_interval_minutes`).
- **Settings:** "Restart the task if it fails", with no restart limit — the
  box should come back after a crash or an unattended reboot without the
  developer touching it. Logs go to `logs_dir` (ADR 0007 rules 4 and 5), never
  to the task's own output, so a restarted task loses no history.

## Checking it works

1. Run `box-worker --once` by hand first, as `agent`, and read the new log
   file under `logs_dir` for errors before trusting the scheduled task.
2. Within `status_interval_minutes` of the task starting, the engine repo
   should show a pinned, locked issue titled "Box status" (created once,
   then only its body updated).
3. If any configured target repo has a `ready-for-agent` ticket on the
   frontier, the box status issue's body should show it as the box's current
   ticket within one `poll_interval_minutes` tick.
4. `logs_dir` should contain a rotating `box-worker.log` with entries for
   every tick; no `agy` output should ever appear in the box status issue,
   an alert issue, or an escalation issue — only fixed-template text does
   (ADR 0007 rule 4).
