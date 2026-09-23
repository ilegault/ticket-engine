# 12: Bootstrap `new`, plus the planner-instructions template

**What to build:** A brand-new project starts fully wired, and every repo's Cowork planner follows the same rules from one engine template.

**Blocked by:** 10

**Status:** done

**Runner:** any

**Auto-merge:** no

## Acceptance criteria

- [x] `new` mode on an empty directory produces skeleton `AGENTS.md` (with the protocol sections), `CONTEXT.md`, the tests-first ADR, the `.scratch/` layout, CI with the gates, the engine config and caller workflows.
- [x] The Cowork planning instructions live in the engine as a template, rendered per repo by both modes.
- [x] The template's rules include: the five status words exactly; every ticket carries `Runner` and `Auto-merge`; approval-required tickets placed as late in the dependency graph as possible; roles, never people; the planner hands off by pushing tickets.
- [x] Tests assert the generated tree for `new` and the rendered template for a sample repo.

## Comments

Added `new()` pure core and `run_new()` adapter producing 12 files (AGENTS.md, CONTEXT.md,
tests-first ADR, .scratch/ layout, CI, engine config, caller workflows, ticket template,
tests-first script, planner instructions). Added `render_planner_instructions(repo_name)`
template with all 5 status words, Runner/Auto-merge table, dependency-ordering rule, roles-not-people,
and push handoff. `adopt()` now renders planner instructions per repo. 27 new tests; 223 pass.
