# Issue tracker: local Markdown

Issues and specs for this repo live as markdown files in `.scratch/`.

`.scratch/` is **tracked in git**, deliberately. The ticket file travels with the
branch, so a draft PR plus its CI run is a complete handoff with nothing written
down anywhere a reviewer cannot reach.

`.claude/` and `Claude outputs/` are gitignored. Nothing an implementer or
reviewer needs may be written there.

## Conventions

- One effort per directory: `.scratch/<effort-slug>/`
- The spec is `.scratch/<effort-slug>/spec.md`
- Tickets are one file each at `.scratch/<effort-slug>/issues/<NN>-<slug>.md`,
  numbered from `01`. Never a single combined tickets file.
- A `Status:` line near the top records triage state
- A `Blocked by:` line near the top names the tickets that must finish first, or
  `None`
- Acceptance criteria are `- [ ]` checkboxes, ticked individually as they are met
- Conversation appends at the bottom under a `## Comments` heading, dated `YYYY-MM-DD`

## Writing acceptance criteria

The implementer has none of the planner's context. Each criterion:

- names its proof: what a test asserts, in observable terms (message text, a
  view's `callback_id`, a row written or its absence);
- points at the existing code to copy by file and function, rather than citing an
  invariant by number;
- promises only what the platform can do (check the Slack/GitHub API first);
- says what tests may fake (the Slack client, the network) and what must be real
  (the lookup or write under change, on a temporary copy of any file).

About five criteria, or one handler, per ticket; split bigger work with
`Blocked by:`. Roles, never names, anywhere in the ticket.

## Status vocabulary

Exactly these five words, and nothing else:

| Status | Means |
|---|---|
| `ready-for-agent` | unclaimed and implementable |
| `in-progress` | claimed; set this before writing any code |
| `done` | landed, criteria all ticked |
| `blocked` | escalated — see the `## Comments` entry and the draft PR |
| `ready-for-developer` | needs a person: a Slack action, a server deploy, a lab decision. **An agent must not claim it.** |

Three spellings of "finished" make the frontier unreadable by the next tool that
opens the repo. Use the word in the table. `human-task` is a legacy spelling of
`ready-for-developer`; the dispatcher reports it as unreadable.

## Working the frontier

Scan `.scratch/<effort>/issues/` for files that are `ready-for-agent` and whose
`Blocked by:` names only `done` tickets. Lowest number wins. Set `Status:` to
`in-progress` and save *before* starting.

## When a skill says "publish to the issue tracker"

Create a new file under `.scratch/<effort-slug>/`, creating the directory if needed.

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The path or the issue number is normally
passed directly.
