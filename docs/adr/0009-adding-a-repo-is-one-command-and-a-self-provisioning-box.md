# ADR 0009 — Adding a repo is one command plus a box that provisions itself

**Status:** accepted
**Date:** 2026-10-06
**Applies to:** the bootstrap, `engine-repos.toml`, the dispatcher's box deferral, and the box's repo list

## Context

Wiring TDS-T8 to the engine took about fifteen manual steps: run adopt, click
through repo settings, create the ruleset and labels, set secrets, allow the
engine's workflows, edit `engine-repos.toml`, then on the box clone the repo,
install its packages, add a `[[repos]]` block and restart the worker.

The engine already computed most of the GitHub side (`github_setup()`), but
nothing applied it, and the bootstrap had no command-line entry point at all.
The box cannot be reached from outside (ADR 0007 rule 6), so no command on the
developer's PC can configure it directly; the only channel to the box is GitHub.

There were also two on-switches that could disagree: the box's local
`[[repos]]` list and the target repo's `box_enabled` flag, which tells the
dispatcher to leave the repo to the box. If `box_enabled` is true and the box
does not list the repo, the repo's tickets stall forever.

## Decision

1. **`add-repo owner/name` is an engine console command run on the developer's
   PC.** It acts through the developer's own `gh` login (repo admin), so it needs
   no new token. It is idempotent: it applies only what is missing, and
   `--check` prints what it would do and changes nothing. `--no-jules` marks a
   repo whose suite cannot pass on Linux.
2. **It does, in order:** verify that both tokens (the box token and
   `PIPELINE_TOKEN`) can reach the repo, stopping with the settings link if not;
   apply repo settings, labels, secrets from `secrets.env`, and permission to use
   the engine's workflows; open the adopt PR and the `engine-repos.toml` PR; wait
   for the adopt PR to merge; then create the ruleset and run the dispatcher dry
   run. If interrupted, re-running resumes. It prints the Jules setup script
   unless `--no-jules`.
3. **On an already-adopted repo it upgrades.** It opens one PR that brings
   `.ticket-engine.toml` up to date: adds missing keys with their defaults and
   removes retired keys (`box_enabled`).
4. **`engine-repos.toml` is the single repo list and the single on-switch.** Its
   entries are `[[repos]]` tables with `repo` and `box` (default `true`). The
   morning report, the box and the dispatcher all read it. Merging the PR that
   adds an entry is what turns a repo on.
5. **`box_enabled` is retired.** The dispatcher reads `engine-repos.toml` from the
   engine's default branch (not the pinned engine version, which would be stale)
   to decide whether the box covers a repo.
6. **Token repo lists stay manual.** Adding a repo to the two fine-grained tokens
   is a developer click; ADR 0007 rule 3 is unchanged. `add-repo` checks it
   rather than doing it.
7. **The box's local config no longer lists repos.** A local config that still has
   `[[repos]]`, or is missing or unparseable, makes the box refuse to start with
   one clear log line.

## Consequences

- Adding a repo is one command, two token clicks, two merges and (for Jules
  repos) one paste of the Jules setup script.
- Slackbot and TDS-T8, wired the old way, are brought forward by running
  `add-repo` on them.
- A wrong order of merges cannot stall a repo: the box treats a repo with no
  `.ticket-engine.toml` on its default branch as not ready (ADR 0010).
- Replacing both tokens with a GitHub App would remove the token clicks. It is
  deliberately not done here.
