# 12: Bootstrap `new`, plus the planner-instructions template

**What to build:** A brand-new project starts fully wired, and every repo's Cowork planner follows the same rules from one engine template.

**Blocked by:** 10

**Status:** in-progress

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [ ] `new` mode on an empty directory produces skeleton `AGENTS.md` (with the protocol sections), `CONTEXT.md`, the tests-first ADR, the `.scratch/` layout, CI with the gates, the engine config and caller workflows.
- [ ] The Cowork planning instructions live in the engine as a template, rendered per repo by both modes.
- [ ] The template's rules include: the five status words exactly; every ticket carries `Runner` and `Auto-merge`; approval-required tickets placed as late in the dependency graph as possible; roles, never people; the planner hands off by pushing tickets.
- [ ] Tests assert the generated tree for `new` and the rendered template for a sample repo.

## Comments
