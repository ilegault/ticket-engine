# 35: Set up the box and run a practice ticket

**What to build:** The developer sets up the box by following `docs/box-setup.md` (ticket 32), then runs one throwaway ticket through the whole path before relying on the box overnight. The run confirms the facts the spec could not check against real `agy`, and the developer records them here so a follow-up ticket can correct any config default. Spec: `.scratch/box-primary-worker/spec.md` (Further Notes).

**Blocked by:** 31, 32

**Status:** ready-for-developer

**Runner:** windows

**Auto-merge:** no

**An agent must not claim this ticket.** It needs the physical box, an interactive Google login and a GitHub token.

## Acceptance criteria

- [ ] Box set up by `docs/box-setup.md` end to end. Any step that did not work as written is noted under `## Comments` for a follow-up doc ticket.
- [ ] `box-worker --once` runs as `agent`. The engine repo's `Box status` issue exists, pinned and locked, and its body reads `State: idle` or `State: working` with a check-in time within the last 30 minutes.
- [ ] A throwaway `ready-for-agent` ticket in a scratch effort of a target repo is claimed by the box and gets a claim branch whose ticket file shows `Claimed-by: box`. A PR is opened by the box and CI and the integrity gate run on it.
- [ ] Recorded under `## Comments`, from the real run and the box's `logs/`:
  - the exact `agy` JSON `status` and message wording for a quota error and for a logged-out run (log out once on purpose);
  - whether any reset time appears in the JSON;
  - the unit `--print-timeout` accepts;
  - what status a timed-out run ends with (set `print_timeout` low once on purpose);
  - the character length of the assembled prompt against the 32,767 limit.

  Only states and wording go in the ticket, never tokens or log dumps.
- [ ] Rebooted the box once mid-ticket. After restart the box resumed the same ticket from its pushed branch without a second claim.

## Comments
