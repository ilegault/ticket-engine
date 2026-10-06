# Spec: add a repo with one command; the box provisions itself

**Status:** ready-for-agent
**Binding:** ADR 0009, ADR 0010 (and ADR 0002, 0006, 0007 where they touch the same code)
**Glossary:** *Add-repo*, *Repo list*, *Repo environment*, *Baseline*, *Not ready*, *Box pause* (CONTEXT.md)

## Problem

Wiring a target repo to the engine took about fifteen manual steps across the
developer's PC, GitHub's settings pages and the box. `github_setup()` worked out
which GitHub settings were missing but nothing applied them, and the bootstrap had
no command at all. On the box, every repo was cloned, given packages and listed in
`~/.ticket-engine-local.toml` by hand, and every repo's tests ran with whatever
`python` happened to be on the PATH.

## What changes

### On the developer's PC: `add-repo owner/name`

A new console script, run from the developer's engine checkout. It acts through
the developer's own `gh` login and is idempotent.

1. Reads the repo's state with read-only `gh` calls and works out a plan.
   `--check` prints the plan and stops.
2. Stops with a settings link if `PIPELINE_TOKEN` (from the secrets file) cannot
   reach the repo.
3. Applies GitHub settings: secrets, labels, auto-merge, secret scanning, push
   protection, Actions enabled with all actions allowed. Workflow permissions are
   left at read.
4. Opens the adopt PR (or, on an already-adopted repo, an upgrade PR for
   `.ticket-engine.toml`) and the PR adding the repo to `engine-repos.toml`.
5. Waits for the adopt PR to merge, then creates the ruleset, runs the
   dispatcher dry run, and prints the Jules setup script (unless `--no-jules`).

### The repo list

`engine-repos.toml` holds `[[repos]]` entries with `repo` and `box` (default
`true`). It is read by the morning report, the box (each tick, through the GitHub
API) and the dispatcher (from the engine's default branch). `box_enabled` is
retired.

### Per-repo config

`.ticket-engine.toml` gains `python_version` (default `"3.12"`), `install`
(default `"pip install -e .[dev]"`) and `jules_enabled` (default `true`).

### On the box

- Its local config has no `[[repos]]`. A config that is missing, unreadable or
  still lists repos makes `box-worker` refuse to start.
- It clones each listed `box = true` repo to `<projects_dir>/<name>` and keeps one
  Python environment per repo at `<envs_dir>/<name>`, rebuilt when the repo's
  dependency files change. agy and Sonnet run with that environment first on PATH.
- Before claiming in a repo it checks: token reach, `.ticket-engine.toml` present,
  environment built, baseline (`gate_commands`) green. A repo failing any check is
  *not ready*: skipped, shown on the box status issue, one alert raised.
- `BOX_PAUSED` on the engine repo stops new claims; status shows
  `paused_by_developer`.
- A repo dropped from the list gets no new claims; its in-flight work finishes.

### The dispatcher

It reads the repo list instead of `box_enabled`. A repo the box lists as not
ready, or a box paused by the developer, counts as a paused box, so Jules may
overflow. `jules_enabled = false` stops Jules in that repo entirely.

## Out of scope

- Running more than one agy session at once on the box. Recorded follow-up.
- Replacing the two fine-grained tokens with a GitHub App (ADR 0009 rule 6).
- Setting the Jules setup script through an API; it stays a paste.
- Deleting anything on the box when a repo leaves the list.

## Tickets

`.scratch/add-repo/issues/53`–`68`, then phase-1 ticket 15 (RBL), rewritten.
