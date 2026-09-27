# 42: Practice run: the box actually falls back to Sonnet

**What to build:** With tickets 39-41 landed, log the box's `agent` Windows account into a Claude subscription once, interactively (the same one-time step ADR 0007 already required for agy's Google login), set `sonnet_enabled = true` in `~/.ticket-engine-local.toml`, and run a real ticket through to confirm the fallback actually fires against the real `claude` CLI and a real (or deliberately forced) agy quota failure — the same shape as the box-primary-worker effort's own practice run. Spec: `.scratch/sonnet-quota-fallback/spec.md` (§Further Notes).

**Blocked by:** 41

**Status:** ready-for-developer

**Runner:** windows

**Auto-merge:** yes

## Acceptance criteria

This is bench work, not something an agent can claim — it needs the developer's hands on the physical box and an interactive `claude` login.

- [ ] The `agent` account has a working `claude` login, confirmed by a plain `claude -p "say hello"` run succeeding non-interactively.
- [ ] A throwaway ticket, run with `sonnet_enabled = true`, is forced into the Sonnet path (for example by temporarily pointing agy at an invalid binary, or another deliberate way of forcing a quota-shaped failure) and lands successfully via Sonnet, with the box's local log showing the fallback fired.
- [ ] The assumptions flagged in the spec's Further Notes are confirmed or corrected: that `stream-json`'s `system/api_retry` events actually appear for a real quota-like condition, that the `--bare`-less session needs nothing beyond the one login, and that the assembled prompt does not exceed any input limit the `claude` CLI enforces on Windows.
- [ ] Any correction from the above is written back into the spec or filed as a follow-up ticket, not silently worked around.

## Comments

<!-- The developer records the outcome here once run. -->
